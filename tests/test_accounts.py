"""Tests for multi-account registry functionality."""
import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from accounts import (
    AccountRecord,
    AntigravityAccountRegistry,
    _account_id_from_email,
    REGISTRY_VERSION,
)


# ── Helpers ─────────────────────────────────────────────────────

LEGACY_PATHS = [
    "accounts.LEGACY_CACHE_FILE",
    "accounts.LEGACY_PI_AUTH",
    "accounts.LEGACY_GEMINI_TOKEN",
]


def _patched_legacy_paths(tmpdir):
    """Return a list of patch contexts that disable all legacy paths."""
    nonexistent = os.path.join(tmpdir, "nonexistent")
    return [
        patch(path, Path(nonexistent) / f"nonexistent-{i}.json")
        for i, path in enumerate(LEGACY_PATHS)
    ]


def _make_registry(tmpdir, filename="registry.json"):
    """Create a registry instance with legacy paths disabled."""
    path = os.path.join(tmpdir, filename)
    patches = _patched_legacy_paths(tmpdir)
    for p in patches:
        p.start()
    reg = AntigravityAccountRegistry(registry_path=path)
    # Don't stop patches — keep them active for registry lifetime
    return reg, patches


# ── Unit tests (no registry needed) ─────────────────────────────

def test_account_id_from_email_deterministic():
    id1 = _account_id_from_email("user@example.com")
    id2 = _account_id_from_email("user@example.com")
    id3 = _account_id_from_email("USER@example.com")
    assert id1 == id2 == id3
    assert len(id1) == 16


def test_account_id_from_email_unique_different_emails():
    id1 = _account_id_from_email("alice@example.com")
    id2 = _account_id_from_email("bob@example.com")
    assert id1 != id2


def test_account_record_to_dict_roundtrip():
    creds = {
        "access_token": "test-access",
        "refresh_token": "test-refresh",
        "expires_at": 9999999999.0,
        "project_id": "test-project",
    }
    record = AccountRecord(
        email="test@example.com",
        credentials=creds,
        created_at=1000.0,
        last_used=2000.0,
    )
    data = record.to_dict()
    restored = AccountRecord.from_dict(data)
    assert restored.email == "test@example.com"
    assert restored.account_id == record.account_id
    assert restored.credentials == creds
    assert restored.created_at == 1000.0
    assert restored.last_used == 2000.0


def test_account_record_is_expired_missing_token():
    record = AccountRecord(email="test@example.com", credentials={})
    assert record.is_expired is True


def test_account_record_is_expired_expired_token():
    record = AccountRecord(
        email="test@example.com",
        credentials={"access_token": "tok", "expires_at": time.time() - 100},
    )
    assert record.is_expired is True


def test_account_record_is_expired_valid_token():
    record = AccountRecord(
        email="test@example.com",
        credentials={"access_token": "tok", "expires_at": time.time() + 3600},
    )
    assert record.is_expired is False


def test_account_record_is_expired_5min_buffer():
    record = AccountRecord(
        email="test@example.com",
        credentials={"access_token": "tok", "expires_at": time.time() + 200},
    )
    assert record.is_expired is True


def test_account_record_has_refresh_token():
    record = AccountRecord(
        email="test@example.com",
        credentials={"refresh_token": "r-tok"},
    )
    assert record.has_refresh_token is True

    record2 = AccountRecord(email="test@example.com", credentials={})
    assert record2.has_refresh_token is False


# ── Registry tests ──────────────────────────────────────────────

class TestAntigravityAccountRegistry:

    def test_new_registry_is_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg, _ = _make_registry(tmpdir)
            assert reg.account_count == 0
            assert reg.active_account_id is None
            assert reg.get_account() is None

    def test_add_account(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg, _ = _make_registry(tmpdir)
            creds = {
                "access_token": "at-123",
                "refresh_token": "rt-456",
                "expires_at": 9999999999.0,
                "project_id": "proj-xyz",
            }
            account = reg.add_account(email="alice@example.com", credentials=creds)
            assert reg.account_count == 1
            assert reg.active_account_id == account.account_id
            assert account.email == "alice@example.com"
            assert account.credentials == creds

    def test_add_account_persists_to_disk(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg, patches = _make_registry(tmpdir)
            reg.add_account(email="alice@example.com", credentials={"access_token": "tok"})
            for p in patches:
                p.stop()

            # Reload without patches (no legacy migration)
            reg2 = AntigravityAccountRegistry(registry_path=os.path.join(tmpdir, "registry.json"))
            assert reg2.account_count == 1
            acc = reg2.get_account()
            assert acc is not None
            assert acc.email == "alice@example.com"

    def test_add_multiple_accounts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg, _ = _make_registry(tmpdir)
            reg.add_account(email="alice@example.com", credentials={"access_token": "a"})
            reg.add_account(email="bob@example.com", credentials={"access_token": "b"})
            reg.add_account(email="carol@example.com", credentials={"access_token": "c"})
            assert reg.account_count == 3

    def test_set_active_account(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg, _ = _make_registry(tmpdir)
            reg.add_account(email="alice@example.com", credentials={"access_token": "a"})
            reg.add_account(email="bob@example.com", credentials={"access_token": "b"})

            active = reg.get_account()
            assert active is not None
            assert active.email == "bob@example.com"

            result = reg.set_active("alice@example.com")
            assert result is not None
            assert result.email == "alice@example.com"
            assert reg.active_account_id == result.account_id

    def test_set_active_by_account_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg, _ = _make_registry(tmpdir)
            acc = reg.add_account(email="alice@example.com", credentials={"access_token": "a"})
            reg.add_account(email="bob@example.com", credentials={"access_token": "b"})

            result = reg.set_active(acc.account_id)
            assert result is not None
            assert result.email == "alice@example.com"

    def test_set_active_nonexistent_account(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg, _ = _make_registry(tmpdir)
            result = reg.set_active("nonexistent@example.com")
            assert result is None

    def test_remove_account_by_email(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg, _ = _make_registry(tmpdir)
            reg.add_account(email="alice@example.com", credentials={"access_token": "a"})
            reg.add_account(email="bob@example.com", credentials={"access_token": "b"})

            assert reg.remove_account("alice@example.com") is True
            assert reg.account_count == 1

            bob = reg.get_account("bob@example.com")
            assert bob is not None
            assert bob.email == "bob@example.com"

    def test_remove_account_by_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg, _ = _make_registry(tmpdir)
            acc = reg.add_account(email="alice@example.com", credentials={"access_token": "a"})
            assert reg.remove_account(acc.account_id) is True
            assert reg.account_count == 0

    def test_remove_active_account_clears_active(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg, _ = _make_registry(tmpdir)
            reg.add_account(email="alice@example.com", credentials={"access_token": "a"})
            reg.add_account(email="bob@example.com", credentials={"access_token": "b"})

            active_id = reg.active_account_id
            assert active_id is not None
            reg.remove_account(active_id)

            assert reg.active_account_id is not None
            assert reg.active_account_id != active_id

    def test_remove_nonexistent_account(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg, _ = _make_registry(tmpdir)
            assert reg.remove_account("nonexistent@example.com") is False

    def test_update_credentials(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg, _ = _make_registry(tmpdir)
            reg.add_account(
                email="alice@example.com",
                credentials={"access_token": "old", "refresh_token": "rt"},
            )

            new_creds = {
                "access_token": "new-token",
                "refresh_token": "rt",
                "expires_at": 9999999999.0,
                "project_id": "proj-new",
            }
            reg.update_credentials("alice@example.com", new_creds)

            acc = reg.get_account("alice@example.com")
            assert acc is not None
            assert acc.credentials["access_token"] == "new-token"
            assert acc.credentials["project_id"] == "proj-new"

    def test_list_accounts_sorted_by_last_used(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg, _ = _make_registry(tmpdir)
            reg.add_account(email="alice@example.com", credentials={"access_token": "a"})
            reg.add_account(email="bob@example.com", credentials={"access_token": "b"})
            reg.add_account(email="carol@example.com", credentials={"access_token": "c"})

            time.sleep(0.01)
            reg.touch("alice@example.com")

            accounts = reg.list_accounts()
            assert len(accounts) == 3
            assert accounts[0].email == "alice@example.com"

    def test_migrate_legacy_single_account(self):
        """Legacy single-account format should be migrated on first load."""
        with tempfile.TemporaryDirectory() as tmpdir:
            legacy_path = os.path.join(tmpdir, "legacy-auth.json")
            legacy_data = {
                "access_token": "legacy-access-token",
                "refresh_token": "legacy-refresh-token",
                "expires_at": 9999999999.0,
                "project_id": "legacy-project",
            }
            with open(legacy_path, "w") as f:
                json.dump(legacy_data, f)

            # Only patch the primary legacy file; others to nonexistent
            with patch("accounts.LEGACY_CACHE_FILE", Path(legacy_path)):
                with patch("accounts.LEGACY_PI_AUTH", Path(tmpdir) / "nonexistent-pi.json"):
                    with patch("accounts.LEGACY_GEMINI_TOKEN", Path(tmpdir) / "nonexistent-gem.json"):
                        registry_path = os.path.join(tmpdir, "registry.json")
                        reg = AntigravityAccountRegistry(registry_path=registry_path)

            assert reg.account_count == 1
            acc = reg.get_account()
            assert acc is not None
            assert acc.credentials["access_token"] == "legacy-access-token"
            assert acc.email == "migrated@legacy"

            reg2 = AntigravityAccountRegistry(registry_path=registry_path)
            assert reg2.account_count == 1

    def test_version_mismatch_triggers_migration(self):
        """Loading a registry with wrong version should trigger migration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = os.path.join(tmpdir, "registry.json")
            with open(registry_path, "w") as f:
                json.dump({"version": 1, "active_account": None, "accounts": {}}, f)

            with patch("accounts.LEGACY_CACHE_FILE", Path(tmpdir) / "nonexistent.json"):
                with patch("accounts.LEGACY_PI_AUTH", Path(tmpdir) / "nonexistent2.json"):
                    with patch("accounts.LEGACY_GEMINI_TOKEN", Path(tmpdir) / "nonexistent3.json"):
                        reg = AntigravityAccountRegistry(registry_path=registry_path)

            assert reg.account_count == 0

    def test_concurrent_access_safety(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "registry.json")
            with patch("accounts.LEGACY_CACHE_FILE", Path(tmpdir) / "nonexistent.json"):
                with patch("accounts.LEGACY_PI_AUTH", Path(tmpdir) / "nonexistent2.json"):
                    with patch("accounts.LEGACY_GEMINI_TOKEN", Path(tmpdir) / "nonexistent3.json"):
                        reg1 = AntigravityAccountRegistry(registry_path=path)
                        reg1.add_account(email="alice@example.com", credentials={"access_token": "a"})

                        reg2 = AntigravityAccountRegistry(registry_path=path)
                        reg2.add_account(email="bob@example.com", credentials={"access_token": "b"})

                        reg3 = AntigravityAccountRegistry(registry_path=path)
                        assert reg3.account_count == 2

    def test_registry_file_permissions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg, _ = _make_registry(tmpdir)
            reg.add_account(email="alice@example.com", credentials={"access_token": "a"})
            mode = os.stat(os.path.join(tmpdir, "registry.json")).st_mode
            assert mode & 0o777 == 0o600


# ── Edge case tests ─────────────────────────────────────────────

class TestEdgeCases:

    def test_empty_registry_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "registry.json")
            with open(path, "w") as f:
                f.write("")

            with patch("accounts.LEGACY_CACHE_FILE", Path(tmpdir) / "nonexistent.json"):
                with patch("accounts.LEGACY_PI_AUTH", Path(tmpdir) / "nonexistent2.json"):
                    with patch("accounts.LEGACY_GEMINI_TOKEN", Path(tmpdir) / "nonexistent3.json"):
                        reg = AntigravityAccountRegistry(registry_path=path)
                        assert reg.account_count == 0

    def test_corrupted_registry_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "registry.json")
            with open(path, "w") as f:
                f.write("{not valid json")

            with patch("accounts.LEGACY_CACHE_FILE", Path(tmpdir) / "nonexistent.json"):
                with patch("accounts.LEGACY_PI_AUTH", Path(tmpdir) / "nonexistent2.json"):
                    with patch("accounts.LEGACY_GEMINI_TOKEN", Path(tmpdir) / "nonexistent3.json"):
                        reg = AntigravityAccountRegistry(registry_path=path)
                        assert reg.account_count == 0

    def test_add_account_same_email_updates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg, _ = _make_registry(tmpdir)
            reg.add_account(email="alice@example.com", credentials={"access_token": "old"})
            reg.add_account(email="alice@example.com", credentials={"access_token": "new"})
            assert reg.account_count == 1
            acc = reg.get_account("alice@example.com")
            assert acc is not None
            assert acc.credentials["access_token"] == "new"

    def test_special_characters_in_email(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg, _ = _make_registry(tmpdir)
            email = "user+tag@sub.example.com"
            reg.add_account(email=email, credentials={"access_token": "tok"})
            acc = reg.get_account(email)
            assert acc is not None
            assert acc.email == email.lower()

    def test_email_case_insensitive_lookup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg, _ = _make_registry(tmpdir)
            reg.add_account(email="Alice@Example.com", credentials={"access_token": "tok"})
            acc = reg.get_account("ALICE@EXAMPLE.COM")
            assert acc is not None
            assert acc.email == "alice@example.com"

    def test_touch_updates_timestamp(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg, _ = _make_registry(tmpdir)
            acc = reg.add_account(email="alice@example.com", credentials={"access_token": "tok"})
            old_ts = acc.last_used
            time.sleep(0.01)
            reg.touch("alice@example.com")
            acc2 = reg.get_account("alice@example.com")
            assert acc2 is not None
            assert acc2.last_used > old_ts
