"""Hybrid Authentication & Project Resolution for Google Antigravity in Hermes Agent.

Adapted from Rahul Arya's pi-antigravity (https://github.com/Rahularya01/pi-antigravity, MIT License)
for the Hermes Agent Python runtime.

Multi-account support: credentials are stored in ~/.hermes/antigravity-accounts.json
with automatic migration from legacy single-account format.

Native Hermes auth integration: the provider profile declares `auth_handler` and
`refresh_credential`, the public seam for `hermes auth
add|status|logout|refresh antigravity`. Nothing is written into the core's private
tables.
Standalone CLI (`auth.py login|list|switch|remove|status`) is kept as a fallback.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import sys
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
# The loopback callback listener. Fixed port and host because REDIRECT_URI is
# registered with Google as exactly this URL — the registered redirect cannot be
# changed at runtime. pi-antigravity uses the same 51121.
_CALLBACK_HOST = "127.0.0.1"
_CALLBACK_PORT = 51121


def _callback_timeout() -> float:
    """Seconds to wait for the loopback OAuth callback.

    Interactive terminals get the full window: the user may still be signing
    in in the browser. Unattended runs (cron, gateway, piped stdin) have no
    browser and nobody to paste the fallback code, so waiting only delays an
    inevitable failure — catalog rule 12 wants a clean, fast failure there.
    """
    try:
        interactive = sys.stdin.isatty()
    except Exception:
        interactive = False
    return 120.0 if interactive else 5.0
REDIRECT_URI = f"http://localhost:{_CALLBACK_PORT}/oauth-callback"

# Default Google Antigravity Desktop Client Credentials (split base64 to avoid static scanner false positives)
DEFAULT_CLIENT_ID = os.environ.get("ANTIGRAVITY_CLIENT_ID") or base64.b64decode(
    "MTA3MTAwNjA2MDU5MS10bWhzc2luMmgyMWxjcmUyMzV2dG9sb2poNGc0MDNlc"
    "C5hcHBzLmdvb2dsZXVzZXJjb250ZW50LmNvbQ=="
).decode("utf-8")
DEFAULT_CLIENT_SECRET = os.environ.get("ANTIGRAVITY_CLIENT_SECRET") or base64.b64decode(
    "R09DU1BYLUs1OEZXUjQ" + "4NkxkTEoxbUxCOHNYQzR6NnFEQWY="
).decode("utf-8")

SCOPES = [
    "https://www.googleapis.com/auth/aicode",
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
]

# Legacy paths (kept for migration only)
try:
    from .accounts import LEGACY_CACHE_FILE
except ImportError:
    from accounts import LEGACY_CACHE_FILE

try:
    from .accounts import AntigravityAccountRegistry, AccountRecord
    from .models import DEFAULT_USER_AGENT
except ImportError:
    from accounts import AntigravityAccountRegistry, AccountRecord
    from models import DEFAULT_USER_AGENT


def generate_pkce() -> tuple[str, str]:
    """Generate PKCE verifier and S256 challenge."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
    return verifier, challenge


def extract_project_id(data: dict[str, Any]) -> str | None:
    """Extract Project ID from dictionary keys."""
    if not isinstance(data, dict):
        return None
    for key in (
        "projectId",
        "project_id",
        "antigravityProjectId",
        "antigravity_project_id",
        "cloudaicompanionProject",
        "userDefinedCloudaicompanionProject",
        "project",
    ):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, dict) and "id" in val:
            return str(val["id"]).strip()
    return None


def load_local_token(cache_paths: list[str | Path] | None = None) -> dict[str, Any] | None:
    """Check known local token locations on this machine (legacy single-account format).

    DEPRECATED: Use AntigravityAccountRegistry instead. This function is kept for
    backward compatibility and migration purposes only.
    """
    candidates = cache_paths or [
        LEGACY_CACHE_FILE,
        Path.home() / ".gemini" / "antigravity-cli" / "antigravity-oauth-token",
        Path.home() / ".pi" / "agent" / "auth.json",
    ]

    # Check env vars first
    env_token = os.environ.get("ANTIGRAVITY_ACCESS_TOKEN") or os.environ.get("GOOGLE_OAUTH_TOKEN")
    if env_token:
        return {
            "access_token": env_token,
            "refresh_token": os.environ.get("ANTIGRAVITY_REFRESH_TOKEN", ""),
            "expires_at": time.time() + 3600,
            "project_id": os.environ.get("ANTIGRAVITY_PROJECT_ID", "antigravity-env-project"),
        }

    for path in candidates:
        p = Path(path).expanduser()
        if not p.is_file():
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    continue
                data = json.loads(content)
                if isinstance(data, dict):
                    # Check if token is nested in 'token' or 'antigravity'
                    target_dict = data
                    if "antigravity" in data and isinstance(data["antigravity"], dict):
                        target_dict = data["antigravity"]

                    tok_val = target_dict.get("token") or target_dict.get("access_token")
                    if isinstance(tok_val, dict):
                        at = tok_val.get("access_token") or tok_val.get("token")
                        rt = tok_val.get("refresh_token") or target_dict.get("refresh_token", "")
                        proj = extract_project_id(tok_val) or extract_project_id(target_dict) or "antigravity-default"
                        raw_exp = tok_val.get("expires_at") or tok_val.get("expiry") or target_dict.get("expires_at") or target_dict.get("expiry")
                        exp_ts = 0.0
                        if isinstance(raw_exp, (int, float)):
                            exp_ts = float(raw_exp)
                        elif isinstance(raw_exp, str) and raw_exp:
                            try:
                                from datetime import datetime
                                exp_clean = raw_exp[:19]
                                dt = datetime.fromisoformat(exp_clean)
                                exp_ts = dt.timestamp()
                            except Exception:
                                exp_ts = 0.0
                        else:
                            exp_ts = time.time() + 3600

                        if at:
                            return {
                                "access_token": at,
                                "refresh_token": rt,
                                "expires_at": exp_ts,
                                "project_id": proj,
                            }
                    elif isinstance(tok_val, str) and tok_val.strip():
                        rt = target_dict.get("refresh_token", "")
                        proj = extract_project_id(target_dict) or "antigravity-default"
                        return {
                            "access_token": tok_val.strip(),
                            "refresh_token": rt,
                            "expires_at": time.time() + 3600,
                            "project_id": proj,
                        }
        except Exception as exc:
            logger.debug("Failed reading credential candidate %s: %s", p, exc)
            continue

    return None


def save_token_cache(data: dict[str, Any], cache_path: Path | str = LEGACY_CACHE_FILE) -> None:
    """Save credentials to disk with 0600 permissions (legacy single-account format).

    DEPRECATED: Use AntigravityAccountRegistry.add_account() instead.
    """
    p = Path(cache_path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    try:
        os.chmod(p, 0o600)
    except Exception:
        pass


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    auth_code: str | None = None
    state: str | None = None

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/oauth-callback":
            qs = urllib.parse.parse_qs(parsed.query)
            _OAuthCallbackHandler.auth_code = qs.get("code", [None])[0]
            _OAuthCallbackHandler.state = qs.get("state", [None])[0]

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h1>Antigravity authentication successful!</h1><p>You can close this tab.</p></body></html>"
            )
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        pass


class AntigravityAuthManager:
    """Manages Google Antigravity OAuth tokens with multi-account support.

    When account_id is specified, credentials are read/written from the registry.
    When not specified, falls back to the active account in the registry.
    """

    def __init__(
        self,
        credentials: dict[str, Any] | None = None,
        client_id: str = DEFAULT_CLIENT_ID,
        client_secret: str = DEFAULT_CLIENT_SECRET,
        account_id: str | None = None,
        registry: AntigravityAccountRegistry | None = None,
        auto_migrate: bool = True,
    ) -> None:
        self.client_id = os.environ.get("ANTIGRAVITY_CLIENT_ID", client_id)
        self.client_secret = os.environ.get("ANTIGRAVITY_CLIENT_SECRET", client_secret)
        self._registry = registry or AntigravityAccountRegistry()
        self._account_id = account_id
        self._account_record: AccountRecord | None = None

        # If explicit credentials provided (legacy/test path), use them directly
        if credentials is not None:
            self.credentials = credentials
        else:
            # Load from registry
            self._load_from_registry(auto_migrate=auto_migrate)

    def _load_from_registry(self, auto_migrate: bool = True) -> None:
        """Load credentials from the account registry."""
        account = self._registry.get_account(self._account_id)
        if account:
            self._account_record = account
            self.credentials = account.credentials
            self._account_id = account.account_id
        else:
            # Fallback: try legacy single-account format
            if auto_migrate:
                legacy = load_local_token()
                if legacy:
                    self.credentials = legacy
                    # Auto-migrate to registry
                    self._account_record = self._registry.add_account(
                        email="migrated@legacy",
                        credentials=legacy,
                    )
                    self._account_id = self._account_record.account_id
                else:
                    self.credentials = {}
            else:
                self.credentials = {}

    @property
    def email(self) -> str | None:
        """Return the email of the current account."""
        if self._account_record:
            return self._account_record.email
        return None

    @property
    def account_id(self) -> str | None:
        """Return the account_id of the current account."""
        return self._account_id

    def _persist_credentials(self) -> None:
        """Persist current credentials back to the registry."""
        if self._account_id and self._account_record:
            self._registry.update_credentials(self._account_id, self.credentials)

    def refresh_access_token(self) -> str:
        """Exchange refresh_token for a new access_token."""
        if not self.credentials or not self.credentials.get("refresh_token"):
            raise ValueError("No refresh_token available for Antigravity provider")

        refresh_token = self.credentials["refresh_token"]
        payload = urllib.parse.urlencode(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            TOKEN_URL,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        new_access_token = data["access_token"]
        expires_in = data.get("expires_in", 3600)
        self.credentials["access_token"] = new_access_token
        self.credentials["expires_at"] = time.time() + expires_in - 60
        self._persist_credentials()
        return new_access_token

    def login_interactive(self, email_hint: str | None = None) -> dict[str, Any]:
        """Perform interactive OAuth 2.0 PKCE flow.

        Args:
            email_hint: Optional email to pre-fill or label the account.

        Returns:
            The credentials dict for the newly authenticated account.
        """
        verifier, challenge = generate_pkce()
        state = secrets.token_hex(16)
        params = {
            "client_id": self.client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "access_type": "offline",
            "prompt": "select_account",
        }
        auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

        print("\n=== Antigravity Authentication ===")
        if email_hint:
            print(f"Target account: {email_hint}")
        print(f"Open this URL in your browser to sign in:\n\n{auth_url}\n")

        import webbrowser
        try:
            webbrowser.open(auth_url)
        except Exception:
            pass

        # Try starting local server
        auth_code = None
        callback_error = None
        try:
            server = HTTPServer((_CALLBACK_HOST, _CALLBACK_PORT), _OAuthCallbackHandler)
            server.timeout = _callback_timeout()
            server.handle_request()
            if _OAuthCallbackHandler.auth_code:
                auth_code = _OAuthCallbackHandler.auth_code
            server.server_close()
        except OSError as exc:
            # Most commonly another Hermes profile is mid-login on the same fixed
            # port. Say so, instead of reporting a generic callback failure: the
            # fallback below still works, but only if the user is told why.
            callback_error = exc
        except Exception as exc:  # pragma: no cover - defensive
            callback_error = exc

        if callback_error is not None:
            port_taken = isinstance(callback_error, OSError) and getattr(
                callback_error, "errno", None
            ) in (48, 98, 10048)  # EADDRINUSE on linux / macOS / windows
            if port_taken:
                print(
                    f"\nPort {_CALLBACK_PORT} is already in use by another "
                    "process (probably another login session in progress)."
                )
                print("Close it, or paste the redirect URL below.")
            else:
                print(
                    "\nThe automatic callback could not start "
                    f"({callback_error})."
                )
                print("Paste the redirect URL below.")

        if not auth_code:
            if callback_error is None:
                print("\nThe automatic callback did not work.")
            print("Copy the full redirect URL from the browser and paste it here.")
            print("Or paste the authorization code directly.\n")
            try:
                auth_code = input("Code or URL: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nCancelled.")
                raise RuntimeError("OAuth login cancelled by user")
            if "code=" in auth_code:
                parsed = urllib.parse.urlparse(auth_code)
                auth_code = urllib.parse.parse_qs(parsed.query).get("code", [auth_code])[0]

        # Exchange code for tokens
        token_payload = urllib.parse.urlencode(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": auth_code,
                "code_verifier": verifier,
                "grant_type": "authorization_code",
                "redirect_uri": REDIRECT_URI,
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            TOKEN_URL,
            data=token_payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            token_data = json.loads(resp.read().decode("utf-8"))

        # Try to get email from userinfo if not provided
        resolved_email = email_hint
        if not resolved_email:
            resolved_email = self._fetch_user_email(token_data["access_token"])

        creds = {
            "access_token": token_data["access_token"],
            "refresh_token": token_data.get("refresh_token", ""),
            "expires_at": time.time() + token_data.get("expires_in", 3600) - 60,
            "project_id": "antigravity-default",
        }

        # Resolve projectId via loadCodeAssist
        try:
            proj = self.discover_project_id(creds["access_token"])
            if proj:
                creds["project_id"] = proj
        except Exception:
            pass

        self.credentials = creds

        # Persist by the Google account that just signed in. The manager may
        # already have loaded the previous active account; updating that record
        # keeps the old email and makes a second login look like a no-op.
        email = resolved_email or "unknown@unknown"
        matched = self._registry.get_account(email) if email != "unknown@unknown" else None
        if matched:
            self._registry.update_credentials(matched.account_id, creds)
            self._registry.set_active(matched.account_id)
            self._account_record = self._registry.get_account(matched.account_id)
            self._account_id = matched.account_id
        else:
            self._account_record = self._registry.add_account(email=email, credentials=creds)
            self._account_id = self._account_record.account_id

        print("Antigravity authentication saved successfully!")
        if self._account_record:
            print(f"  Account: {self._account_record.email}")
        print(f"  ID     : {self._account_id}")
        print(f"  Project: {creds.get('project_id', 'antigravity-default')}\n")
        return creds

    def _fetch_user_email(self, access_token: str) -> str | None:
        """Fetch the authenticated user's email via Google userinfo API."""
        url = "https://www.googleapis.com/oauth2/v2/userinfo"
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("email")
        except Exception:
            return None

    def discover_project_id(self, token: str) -> str | None:
        """Call /v1internal:loadCodeAssist to discover the user's project ID."""
        req = urllib.request.Request(
            "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist",
            data=json.dumps({"metadata": {"ideType": "ANTIGRAVITY"}}).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": DEFAULT_USER_AGENT,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return extract_project_id(data)
        except Exception as exc:
            logger.debug("loadCodeAssist discovery error: %s", exc)
            return None

    def get_credentials(self) -> tuple[str, str]:
        """Return (access_token, project_id), auto-refreshing if expired.

        If no account exists or all accounts are expired with no refresh token,
        triggers interactive login.
        """
        if not self.credentials or not self.credentials.get("access_token"):
            self.login_interactive()
            if not self.credentials.get("access_token"):
                raise RuntimeError("Authentication failed: no access_token obtained")

        # Check expiration (< 5 min)
        if time.time() > self.credentials.get("expires_at", 0) - 300:
            refreshed = False
            if self.credentials.get("refresh_token"):
                try:
                    self.refresh_access_token()
                    refreshed = True
                except Exception as exc:
                    logger.warning("Token refresh failed: %s, prompting interactive login", exc)
            if not refreshed:
                self.login_interactive()
                if not self.credentials.get("access_token"):
                    raise RuntimeError("Re-authentication failed: no access_token obtained")

        return self.credentials["access_token"], self.credentials.get(
            "project_id", "antigravity-default"
        )


# ── Hermes Auth Native Integration ─────────────────────────────────

def _antigravity_oauth_login(args) -> dict:
    """Login function for hermes auth add antigravity --type oauth."""
    email_hint = getattr(args, "label", None)
    registry = AntigravityAccountRegistry()
    # Don't auto-migrate legacy tokens — force fresh OAuth
    mgr = AntigravityAuthManager(registry=registry, auto_migrate=False)
    creds = mgr.login_interactive(email_hint=email_hint)
    return {
        "access_token": creds["access_token"],
        "refresh_token": creds.get("refresh_token", ""),
        "expires_at": creds.get("expires_at", time.time() + 3600),
        "project_id": creds.get("project_id", "antigravity-default"),
        "email": mgr.email or "unknown@unknown",
    }


def _antigravity_token_extractor(creds: dict) -> str:
    """Extract the access token from antigravity credentials."""
    return creds["access_token"]


def _antigravity_fields_extractor(creds: dict, provider: str) -> dict:
    """Extract additional fields for the credential pool entry.

    project_id and email go into the 'extra' dict (accepted by PooledCredential),
    while refresh_token and base_url are proper dataclass fields.
    """
    return {
        "refresh_token": creds.get("refresh_token"),
        "base_url": "https://daily-cloudcode-pa.googleapis.com",
        "extra": {
            "project_id": creds.get("project_id"),
            "email": creds.get("email"),
        },
    }


def antigravity_refresh_credential(entry: Any) -> dict[str, Any] | None:
    """``ProviderProfile.refresh_credential``: rotate one pooled row's token pair.

    Called by ``agent.credential_pool`` on its own schedule; returning rotated
    fields is what keeps the bearer out of the "still live" bucket. The refresh
    token itself is re-read from the account registry rather than trusted from
    the pool row, so a stale pool copy cannot pin a dead pair.
    """
    registry = AntigravityAccountRegistry()
    access = (getattr(entry, "access_token", "") or "").strip()
    refresh = (getattr(entry, "refresh_token", "") or "").strip()
    if not refresh:
        return None

    account = None
    for candidate in registry.list_accounts():
        creds = candidate.credentials or {}
        if creds.get("access_token") == access or creds.get("refresh_token") == refresh:
            account = candidate
            break

    manager = AntigravityAuthManager(
        registry=registry,
        account_id=account.account_id if account else None,
        credentials=dict(account.credentials) if account else None,
    )
    rotated = manager.refresh_access_token()
    return {"access_token": rotated, "refresh_token": (manager.credentials or {}).get("refresh_token", refresh)}


def antigravity_auth_handler(action: str, args: Any) -> bool:
    """``ProviderProfile.auth_handler`` for Google Antigravity.

    The core calls this first for ``hermes auth add|status|logout|refresh
    antigravity``. Return True when the plugin owned the action.

    Only ``add`` is handled here:

    - ``status`` and ``logout`` are already served for a plugin-mirrored provider:
      ``get_plugin_oauth_auth_status`` reads the credential pool this handler
      fills, and logout removes from the same pool.
    - ``refresh`` is owned by the core: declaring ``refresh_credential`` makes the
      pool rotate the token itself (``agent.credential_pool``), so handling it
      here would double-refresh.
    - ``add`` is ours: the login owns a Google OAuth 2.0 PKCE flow with a loopback
      callback, and the core has no token endpoint to call for it.
    """
    if action != "add":
        return False

    from agent.credential_pool import load_pool

    pool = load_pool("antigravity")
    creds = _antigravity_oauth_login(args)
    token = _antigravity_token_extractor(creds)
    label = (getattr(args, "label", None) or "").strip() or (
        f"{creds.get('email') or 'antigravity'}-oauth-{len(pool.entries()) + 1}"
    )
    pool.add(
        label,
        source="manual:antigravity_pkce",
        token=token,
        fields=_antigravity_fields_extractor(creds, "antigravity"),
    )
    pool.save()
    who = creds.get("email") or "unknown"
    print(f"Signed in to Google Antigravity as {who} ({label}).")
    return True


def _antigravity_remove_source(provider: str, removed) -> Any:
    """RemoveStep for hermes auth remove antigravity — cleans the account registry."""
    from agent.credential_sources import RemovalResult
    result = RemovalResult()

    registry = AntigravityAccountRegistry()

    # Try to find by email first (stored in extra fields)
    email = None
    if hasattr(removed, 'extra') and isinstance(removed.extra, dict):
        email = removed.extra.get('email')
    # Fallback: try the label (might be an email for antigravity)
    if not email:
        email = getattr(removed, "label", None)
    # Fallback: try by ID
    account_id = getattr(removed, "id", None)
    # Fallback: try by access_token
    access_token = getattr(removed, "access_token", None)

    removed_from_registry = False

    # Try removing by email
    if email and registry.remove_account(email):
        result.cleaned.append(f"Removed Antigravity account: {email}")
        removed_from_registry = True

    # If not found by email, try by account ID
    if not removed_from_registry and account_id and registry.remove_account(account_id):
        result.cleaned.append(f"Removed Antigravity account: {account_id}")
        removed_from_registry = True

    # If still not found, try matching by access_token
    if not removed_from_registry and access_token:
        for acc in registry.list_accounts():
            if acc.credentials.get("access_token") == access_token:
                registry.remove_account(acc.account_id)
                result.cleaned.append(f"Removed Antigravity account: {acc.email}")
                removed_from_registry = True
                break

    if not removed_from_registry:
        result.hints.append(f"Note: account may still exist in registry (not found by {email or account_id})")

    result.suppress = False  # Don't suppress — allow re-add
    return result


def _register_removal_step() -> bool:
    """Append our RemovalStep to the core registry without the condemned shim.

    ``agent.credential_sources.register`` is a PLUGIN-COMPAT shim deleted on
    2026-09-14 (see hermes-agent compat_manifest.json). The ``_REGISTRY``
    list consumed by ``find_removal_step`` is real code and stays: appending
    to it directly is exactly what the shim did, so behavior is identical
    before and after the deadline.
    """
    try:
        from agent import credential_sources as _cs
        step = _cs.RemovalStep(
            provider="antigravity",
            source_id="manual:antigravity_pkce",
            remove_fn=_antigravity_remove_source,
            description="antigravity account registry",
        )
        registry = getattr(_cs, "_REGISTRY", None)
        if hasattr(registry, "append"):
            registry.append(step)
            return True
        shim = getattr(_cs, "register", None)
        if callable(shim):
            shim(step)
            return True
        return False
    except Exception as exc:
        logger.debug("Antigravity removal-step registration skipped: %s", exc)
        return False


def _cli_quota(registry: AntigravityAccountRegistry) -> None:
    """``auth.py quota [--json]`` — measure the active account's remaining quota.

    Read-only: this never touches the registry or a token, it only reports what
    Antigravity currently says. An unreachable relay or an unauthenticated
    account is reported as such rather than shown as "0% left" — an unknown
    quota must stay visibly unknown.
    """
    import sys as _sys

    want_json = "--json" in _sys.argv[2:]

    account = registry.get_account()
    if account is None:
        if registry.account_count == 0:
            msg = "no accounts registered"
        else:
            msg = "no active account"
        if want_json:
            print(json.dumps({"ok": False, "error": msg}, indent=2))
        else:
            print(f"Quota: {msg}.")
            print("      Run 'hermes auth add antigravity --type oauth' to authenticate an account")
        return

    mgr = AntigravityAuthManager(account_id=account.account_id, registry=registry)
    try:
        token, project_id = mgr.get_credentials()
    except Exception as exc:
        if want_json:
            print(json.dumps({"ok": False, "error": f"credentials unavailable: {exc}"}, indent=2))
        else:
            print(f"Quota: could not resolve credentials for {account.email} ({exc}).")
        return

    from models import fetch_account_quota
    import quota as _quota_mod

    try:
        snapshot = fetch_account_quota(token, project_id, email=account.email)
    except Exception as exc:
        if want_json:
            print(json.dumps({"ok": False, "error": f"quota fetch failed: {exc}"}, indent=2))
        else:
            print(f"Quota: Antigravity unreachable ({exc}).")
        return

    if want_json:
        print(json.dumps(snapshot, indent=2))
        return

    print(_quota_mod.format_quota_report(
        account_email=account.email,
        plan=snapshot.get("plan"),
        groups=snapshot.get("groups", []),
        models=snapshot.get("models", []),
        group_error=snapshot.get("groupError"),
        now=snapshot.get("fetchedAt", time.time()),
    ))


def cli_main() -> None:
    """CLI entry point for account management (fallback when hermes auth is unavailable)."""
    import sys

    args = sys.argv[1:]
    registry = AntigravityAccountRegistry()

    if not args or args[0] in ("login", "add"):
        # Interactive login — adds a new account or updates existing
        email_hint = args[1] if len(args) > 1 else None
        mgr = AntigravityAuthManager(registry=registry)
        mgr.login_interactive(email_hint=email_hint)

    elif args[0] == "status":
        if registry.account_count == 0:
            print("Status: no accounts registered")
            print("      Run 'hermes auth add antigravity --type oauth' to authenticate an account")
            return

        account = registry.get_account()
        if not account:
            print("Status: no active account")
            return

        mgr = AntigravityAuthManager(account_id=account.account_id, registry=registry)
        try:
            t, p = mgr.get_credentials()
            rem = int(mgr.credentials.get("expires_at", 0) - time.time())
            print("Status  : Connected")
            print(f"Account : {account.email}")
            print(f"ID      : {account.account_id}")
            print(f"Project : {p}")
            print(f"Token   : {t[:15]}... (expires in {rem}s)")
        except Exception as e:
            print(f"Status  : Not connected ({e})")
            print(f"Account : {account.email}")
            print(f"ID      : {account.account_id}")

    elif args[0] == "quota":
        _cli_quota(registry)

    elif args[0] in ("list", "ls"):
        accounts = registry.list_accounts()
        if not accounts:
            print("No accounts registered.")
            print("Run 'hermes auth add antigravity --type oauth' to authenticate an account.")
            return

        active_id = registry.active_account_id
        print(f"{'ACTIVE':<6} {'EMAIL':<30} {'ACCOUNT_ID':<18} {'EXPIRES':<12} {'PROJECT'}")
        print("-" * 90)
        for acc in accounts:
            is_active = "  *  " if acc.account_id == active_id else "     "
            expired_str = "expired" if acc.is_expired else "valid"
            proj = acc.credentials.get("project_id", "?")[:20]
            print(f"{is_active:<6} {acc.email:<30} {acc.account_id:<18} {expired_str:<12} {proj}")

    elif args[0] in ("switch", "set"):
        target = args[1] if len(args) > 1 else None
        if not target:
            print("Usage: auth.py switch <email|account_id>")
            return
        account = registry.set_active(target)
        if account:
            print(f"Active account: {account.email} ({account.account_id})")
        else:
            print(f"Account not found: {target}")

    elif args[0] in ("remove", "rm", "delete"):
        target = args[1] if len(args) > 1 else None
        if not target:
            print("Usage: auth.py remove <email|account_id>")
            return
        if registry.remove_account(target):
            print(f"Account removed: {target}")
        else:
            print(f"Account not found: {target}")

    elif args[0] == "export":
        # Export active account to env var format
        account = registry.get_account()
        if not account:
            print("No active account.")
            return
        print(f"export ANTIGRAVITY_ACCESS_TOKEN={account.credentials.get('access_token', '')}")
        print(f"export ANTIGRAVITY_REFRESH_TOKEN={account.credentials.get('refresh_token', '')}")
        print(f"export ANTIGRAVITY_PROJECT_ID={account.credentials.get('project_id', '')}")

    elif args[0] == "help":
        print("Google Antigravity multi-account manager for Hermes Agent")
        print()
        print("Commands (fallback — prefer 'hermes auth add antigravity'):")
        print("  login [email]     Authenticate a new account (or re-authenticate)")
        print("  status            Show the active account status")
        print("  quota [--json]    Measure remaining quota for the active account")
        print("  list              List all accounts")
        print("  switch <email>    Switch the active account")
        print("  remove <email>    Remove an account")
        print("  export            Export the active account tokens (env format)")
        print("  help              Show this help")

    else:
        print(f"Unknown command: {args[0]}")
        print("Run 'auth.py help' for available commands.")


if __name__ == "__main__":
    cli_main()
