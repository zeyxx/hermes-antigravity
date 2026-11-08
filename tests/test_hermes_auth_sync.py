"""Registry -> Hermes standard credentials sync (antigravity-accounts.json -> auth.json).

The plugin stores OAuth material in its multi-account registry
(``~/.hermes/antigravity-accounts.json``), while Hermes' own auth surface
reads ``~/.hermes/auth.json`` (``providers.<id>`` + ``credential_pool``).
Before this fix `hermes auth status antigravity` reported "logged out"
even with a live registry account, because nothing ever wrote auth.json.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def _sandbox_registry():
    """Registry on a temp path; auth.json must land next to it (sandbox)."""
    import tempfile
    from accounts import AntigravityAccountRegistry

    root = Path(tempfile.mkdtemp(prefix="hermes-auth-sync-"))
    reg = AntigravityAccountRegistry(registry_path=root / "antigravity-accounts.json")
    return reg, root


def _read(store_root):
    path = Path(store_root) / "auth.json"
    assert path.is_file(), f"auth.json was not written in {store_root}"
    return json.loads(path.read_text(encoding="utf-8"))


def _seed_two(reg):
    reg.add_account(email="alice@example.com", credentials={
        "access_token": "ya29.alice", "refresh_token": "rt-alice",
        "expires_at": 9_000_000_000.0, "project_id": "proj-alice"})
    reg.add_account(email="bob@example.com", credentials={
        "access_token": "ya29.bob", "refresh_token": "rt-bob",
        "expires_at": 9_000_000_000.0, "project_id": "proj-bob"})


def test_add_account_syncs_active_account_to_hermes_auth_json():
    reg, root = _sandbox_registry()
    _seed_two(reg)

    store = _read(root)
    state = (store.get("providers") or {}).get("antigravity")
    assert state is not None, "providers.antigravity missing in auth.json"
    assert state["access_token"] == "ya29.bob", "the active (last added) account must be synced"
    assert state["refresh_token"] == "rt-bob"
    # Hermes native expiry is an ISO-8601 string, not the registry epoch float.
    expires_at = state["expires_at"]
    assert isinstance(expires_at, str), f"expires_at must be an ISO string, got {type(expires_at)}"
    parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None

    # The pool row is what the status path leases (load_pool), so it must exist too.
    pool_rows = (store.get("credential_pool") or {}).get("antigravity") or []
    assert any(
        r.get("access_token") == "ya29.bob" and r.get("refresh_token") == "rt-bob"
        for r in pool_rows
    ), f"no pool row for the active account: {pool_rows}"


def test_loading_existing_registry_resyncs_without_a_save():
    reg, root = _sandbox_registry()
    _seed_two(reg)
    os.remove(Path(root) / "auth.json")  # simulate a pre-existing registry, no auth.json yet

    from accounts import AntigravityAccountRegistry
    reloaded = AntigravityAccountRegistry(registry_path=root / "antigravity-accounts.json")
    assert reloaded.get_account().email == "bob@example.com"

    store = _read(root)
    assert store["providers"]["antigravity"]["access_token"] == "ya29.bob"


def test_set_active_resyncs_hermes_auth_json():
    reg, root = _sandbox_registry()
    _seed_two(reg)
    reg.set_active("alice@example.com")

    store = _read(root)
    assert store["providers"]["antigravity"]["access_token"] == "ya29.alice"
    # Multi-account registry is untouched.
    assert reg.account_count == 2
    assert {a.email for a in reg.list_accounts()} == {"alice@example.com", "bob@example.com"}


def test_update_credentials_resyncs_hermes_auth_json():
    reg, root = _sandbox_registry()
    _seed_two(reg)
    reg.update_credentials("bob@example.com", {
        "access_token": "ya29.bob-fresh", "refresh_token": "rt-bob-fresh",
        "expires_at": 9_500_000_000.0, "project_id": "proj-bob"})

    store = _read(root)
    assert store["providers"]["antigravity"]["access_token"] == "ya29.bob-fresh"


def test_existing_auth_json_content_is_preserved():
    reg, root = _sandbox_registry()
    (Path(root) / "auth.json").write_text(json.dumps({
        "version": 1,
        "providers": {"nous": {"access_token": "nous-tok"}},
        "credential_pool": {"nous": [{"id": "x", "access_token": "nous-tok"}]},
        "active_provider": "nous",
    }), encoding="utf-8")
    _seed_two(reg)

    store = _read(root)
    assert store["providers"]["nous"]["access_token"] == "nous-tok"
    assert store["active_provider"] == "nous"
    assert store["providers"]["antigravity"]["access_token"] == "ya29.bob"


def test_empty_registry_writes_no_antigravity_provider():
    reg, root = _sandbox_registry()
    # Touching an empty registry must not invent credentials.
    assert (root / "auth.json").exists() is False or \
        "antigravity" not in (_read(root).get("providers") or {})
