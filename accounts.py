"""Multi-account registry for Google Antigravity OAuth in Hermes Agent.

Handles account storage, migration from legacy single-account format,
active account selection, and edge-case detection (expired tokens,
refresh failures, email mismatch).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Current registry format version
REGISTRY_VERSION = 2

# Paths
DEFAULT_REGISTRY_PATH = Path.home() / ".hermes" / "antigravity-accounts.json"
LEGACY_CACHE_FILE = Path.home() / ".hermes" / "antigravity-auth.json"
LEGACY_PI_AUTH = Path.home() / ".pi" / "agent" / "auth.json"
LEGACY_GEMINI_TOKEN = Path.home() / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"


def _account_id_from_email(email: str) -> str:
    """Generate a stable, opaque account identifier from an email."""
    return hashlib.sha256(email.lower().strip().encode("utf-8")).hexdigest()[:16]


class AccountRecord:
    """Represents a single Antigravity account with its credentials."""

    def __init__(
        self,
        email: str,
        credentials: dict[str, Any],
        account_id: str | None = None,
        created_at: float | None = None,
        last_used: float | None = None,
    ) -> None:
        self.email = email.lower().strip()
        self.account_id = account_id or _account_id_from_email(self.email)
        self.credentials = credentials
        self.created_at = created_at or time.time()
        self.last_used = last_used or time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "email": self.email,
            "account_id": self.account_id,
            "credentials": self.credentials,
            "created_at": self.created_at,
            "last_used": self.last_used,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AccountRecord":
        return cls(
            email=data.get("email", "unknown@unknown"),
            credentials=data.get("credentials", {}),
            account_id=data.get("account_id"),
            created_at=data.get("created_at"),
            last_used=data.get("last_used"),
        )

    @property
    def is_expired(self) -> bool:
        """Check if the access token is expired or missing."""
        if not self.credentials.get("access_token"):
            return True
        expires_at = self.credentials.get("expires_at", 0)
        return time.time() > expires_at - 300  # 5-min buffer

    @property
    def has_refresh_token(self) -> bool:
        return bool(self.credentials.get("refresh_token"))


class AntigravityAccountRegistry:
    """Manages multiple Antigravity accounts with persistent storage."""

    def __init__(self, registry_path: Path | str = DEFAULT_REGISTRY_PATH) -> None:
        self.registry_path = Path(registry_path).expanduser()
        self._data: dict[str, Any] = {"version": REGISTRY_VERSION, "active_account": None, "accounts": {}}
        self._load()

    def _load(self) -> None:
        """Load registry from disk, migrating legacy format if needed."""
        if not self.registry_path.is_file():
            self._migrate_legacy()
            return

        try:
            with open(self.registry_path, "r", encoding="utf-8") as f:
                raw = f.read().strip()
                if not raw:
                    return
                data = json.loads(raw)
            if not isinstance(data, dict):
                return

            # Version check — only load if version matches
            if data.get("version") == REGISTRY_VERSION:
                self._data = data
            else:
                logger.warning(
                    "Registry version mismatch (expected %d, got %s) — migrating",
                    REGISTRY_VERSION,
                    data.get("version"),
                )
                self._migrate_legacy()
        except Exception as exc:
            logger.debug("Failed to load registry: %s", exc)

    def _migrate_legacy(self) -> None:
        """Migrate from legacy single-account format to multi-account registry."""
        legacy_creds = self._load_legacy_credentials()
        if not legacy_creds:
            return

        email = legacy_creds.get("email", "migrated@legacy")
        account = AccountRecord(
            email=email,
            credentials=legacy_creds.get("credentials", legacy_creds),
            created_at=time.time() - 86400,  # Mark as pre-existing
        )
        self._data["accounts"][account.account_id] = account.to_dict()
        self._data["active_account"] = account.account_id
        self._save()
        logger.info("Migrated legacy Antigravity credentials to account registry (%s)", email)

    def _load_legacy_credentials(self) -> dict[str, Any] | None:
        """Try to load credentials from legacy file locations."""
        # Primary legacy: ~/.hermes/antigravity-auth.json
        if LEGACY_CACHE_FILE.is_file():
            try:
                with open(LEGACY_CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.loads(f.read().strip())
                if isinstance(data, dict) and data.get("access_token"):
                    return {
                        "email": data.get("email", "migrated@legacy"),
                        "credentials": {
                            "access_token": data.get("access_token"),
                            "refresh_token": data.get("refresh_token", ""),
                            "expires_at": data.get("expires_at", time.time() + 3600),
                            "project_id": data.get("project_id", "antigravity-default"),
                        },
                    }
            except Exception:
                pass

        # Secondary: ~/.pi/agent/auth.json
        if LEGACY_PI_AUTH.is_file():
            try:
                with open(LEGACY_PI_AUTH, "r", encoding="utf-8") as f:
                    data = json.loads(f.read().strip())
                if isinstance(data, dict):
                    # pi-antigravity format: {"antigravity": {"token": {...}}}
                    ag = data.get("antigravity", data)
                    tok = ag.get("token", ag)
                    if isinstance(tok, dict) and tok.get("access_token"):
                        return {
                            "email": "migrated@pi-legacy",
                            "credentials": {
                                "access_token": tok.get("access_token"),
                                "refresh_token": tok.get("refresh_token", ""),
                                "expires_at": tok.get("expires_at", time.time() + 3600),
                                "project_id": tok.get("project_id", "antigravity-default"),
                            },
                        }
            except Exception:
                pass

        return None

    def _save(self) -> None:
        """Persist registry to disk with 0600 permissions."""
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.registry_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)
        try:
            os.chmod(self.registry_path, 0o600)
        except Exception:
            pass

    # ── Account CRUD ──────────────────────────────────────────────

    def add_account(self, email: str, credentials: dict[str, Any]) -> AccountRecord:
        """Add or update an account with the given email and credentials."""
        account_id = _account_id_from_email(email)
        existing = self._data["accounts"].get(account_id)
        created_at = existing.get("created_at", time.time()) if existing else time.time()

        account = AccountRecord(
            email=email,
            credentials=credentials,
            account_id=account_id,
            created_at=created_at,
            last_used=time.time(),
        )
        self._data["accounts"][account_id] = account.to_dict()
        self._data["active_account"] = account_id
        self._save()
        return account

    def remove_account(self, email_or_id: str) -> bool:
        """Remove an account by email or account_id. Returns True if found and removed."""
        account_id = self._resolve_account_id(email_or_id)
        if account_id and account_id in self._data["accounts"]:
            del self._data["accounts"][account_id]
            # Clear active_account if it was this one
            if self._data.get("active_account") == account_id:
                self._data["active_account"] = next(iter(self._data["accounts"]), None)
            self._save()
            return True
        return False

    def get_account(self, email_or_id: str | None = None) -> AccountRecord | None:
        """Get an account by email/id, or the active account if None specified."""
        if email_or_id is None:
            account_id = self._data.get("active_account")
        else:
            account_id = self._resolve_account_id(email_or_id)

        if account_id and account_id in self._data["accounts"]:
            return AccountRecord.from_dict(self._data["accounts"][account_id])
        return None

    def list_accounts(self) -> list[AccountRecord]:
        """Return all accounts sorted by last_used descending."""
        accounts = [
            AccountRecord.from_dict(data)
            for data in self._data["accounts"].values()
        ]
        return sorted(accounts, key=lambda a: a.last_used, reverse=True)

    def set_active(self, email_or_id: str) -> AccountRecord | None:
        """Set the active account by email or account_id."""
        account_id = self._resolve_account_id(email_or_id)
        if account_id and account_id in self._data["accounts"]:
            self._data["active_account"] = account_id
            self._data["accounts"][account_id]["last_used"] = time.time()
            self._save()
            return AccountRecord.from_dict(self._data["accounts"][account_id])
        return None

    def update_credentials(self, email_or_id: str, credentials: dict[str, Any]) -> None:
        """Update credentials for an existing account."""
        account_id = self._resolve_account_id(email_or_id)
        if account_id and account_id in self._data["accounts"]:
            self._data["accounts"][account_id]["credentials"] = credentials
            self._data["accounts"][account_id]["last_used"] = time.time()
            self._save()

    def touch(self, email_or_id: str | None = None) -> None:
        """Update last_used timestamp for an account."""
        account_id = self._resolve_account_id(email_or_id) if email_or_id else self._data.get("active_account")
        if account_id and account_id in self._data["accounts"]:
            self._data["accounts"][account_id]["last_used"] = time.time()
            self._save()

    # ── Helpers ───────────────────────────────────────────────────

    def _resolve_account_id(self, email_or_id: str) -> str | None:
        """Resolve an email or account_id to a canonical account_id."""
        # Direct match
        if email_or_id in self._data["accounts"]:
            return email_or_id
        # Email match
        target_id = _account_id_from_email(email_or_id)
        if target_id in self._data["accounts"]:
            return target_id
        # Case-insensitive email scan
        email_lower = email_or_id.lower().strip()
        for aid, data in self._data["accounts"].items():
            if data.get("email", "").lower() == email_lower:
                return aid
        return None

    @property
    def active_account_id(self) -> str | None:
        return self._data.get("active_account")

    @property
    def account_count(self) -> int:
        return len(self._data["accounts"])
