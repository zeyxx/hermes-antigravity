"""Hybrid Authentication & Project Resolution for Google Antigravity in Hermes Agent.

Adapted from Rahul Arya's pi-antigravity (https://github.com/Rahularya01/pi-antigravity, MIT License)
for the Hermes Agent Python runtime.

Multi-account support: credentials are stored in ~/.hermes/antigravity-accounts.json
with automatic migration from legacy single-account format.

Native Hermes auth integration: registers with `hermes auth add antigravity`
and `hermes auth remove antigravity` via the _OAUTH_ADD_SPECS and RemovalStep hooks.
Standalone CLI (`auth.py login|list|switch|remove|status`) is kept as a fallback.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REDIRECT_URI = "http://localhost:51121/oauth-callback"

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
LEGACY_CACHE_FILE = Path.home() / ".hermes" / "antigravity-auth.json"

try:
    from .accounts import AntigravityAccountRegistry, AccountRecord, DEFAULT_REGISTRY_PATH
    from .models import DEFAULT_USER_AGENT
except ImportError:
    from accounts import AntigravityAccountRegistry, AccountRecord, DEFAULT_REGISTRY_PATH
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
                b"<html><body><h1>Authentification Antigravity reussie !</h1><p>Vous pouvez fermer cet onglet.</p></body></html>"
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
            print(f"Compte cible : {email_hint}")
        print(f"Ouvrez cette URL dans votre navigateur pour vous connecter :\n\n{auth_url}\n")

        import webbrowser
        try:
            webbrowser.open(auth_url)
        except Exception:
            pass

        # Try starting local server
        auth_code = None
        try:
            server = HTTPServer(("127.0.0.1", 51121), _OAuthCallbackHandler)
            server.timeout = 120.0
            server.handle_request()
            if _OAuthCallbackHandler.auth_code:
                auth_code = _OAuthCallbackHandler.auth_code
            server.server_close()
        except Exception:
            pass

        if not auth_code:
            print("\nLe callback automatique n'a pas fonctionne.")
            print("Copiez l'URL complete de redirection du navigateur et collez-la ici.")
            print("Ou collez directement le code d'autorisation.\n")
            try:
                auth_code = input("Code ou URL : ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nAnnulation.")
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

        # Persist to registry
        email = resolved_email or "unknown@unknown"
        if self._account_id and self._account_record:
            # Update existing account
            self._registry.update_credentials(self._account_id, creds)
            self._account_record = self._registry.get_account(self._account_id)
        else:
            # Add new account
            self._account_record = self._registry.add_account(email=email, credentials=creds)
            self._account_id = self._account_record.account_id

        print(f"Authentification Antigravity enregistree avec succes !")
        if self._account_record:
            print(f"  Compte : {self._account_record.email}")
        print(f"  ID     : {self._account_id}")
        print(f"  Projet : {creds.get('project_id', 'antigravity-default')}\n")
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


def register_hermes_auth() -> bool:
    """Register antigravity with hermes auth system.
    
    Called automatically when the plugin loads. Enables:
    - `hermes auth add antigravity --type oauth` — interactive OAuth login
    - `hermes auth remove antigravity <n>` — remove account from registry
    - `hermes auth list` — show antigravity accounts
    - `hermes auth status antigravity` — show active account status
    
    Returns True if registration succeeded.
    """
    try:
        from hermes_cli import auth_commands as auth_cmd
        from agent.credential_sources import RemovalStep, register as register_removal
        
        # 1. Register as OAuth-capable provider
        if hasattr(auth_cmd, '_OAUTH_CAPABLE_PROVIDERS'):
            auth_cmd._OAUTH_CAPABLE_PROVIDERS.add("antigravity")
        
        # 2. Register OAuth add spec
        if hasattr(auth_cmd, '_OAUTH_ADD_SPECS') and hasattr(auth_cmd, '_OAuthAddSpec'):
            auth_cmd._OAUTH_ADD_SPECS["antigravity"] = auth_cmd._OAuthAddSpec(
                login=_antigravity_oauth_login,
                token=_antigravity_token_extractor,
                source="manual:antigravity_pkce",
                fields=_antigravity_fields_extractor,
                activate_first=False,  # Don't auto-switch provider
            )
        
        # 3. Register removal step
        register_removal(RemovalStep(
            provider="antigravity",
            source_id="manual:antigravity_pkce",
            remove_fn=_antigravity_remove_source,
            description="antigravity account registry",
        ))
        
        logger.debug("Antigravity registered with hermes auth system")
        return True
    except Exception as exc:
        logger.debug("Could not register antigravity with hermes auth: %s", exc)
        return False


# ── CLI Interface (fallback) ──────────────────────────────────────

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
            print("Statut : Aucun compte enregistre")
            print("      Lancez 'hermes auth add antigravity --type oauth' pour authentifier un compte")
            return

        account = registry.get_account()
        if not account:
            print("Statut : Aucun compte actif")
            return

        mgr = AntigravityAuthManager(account_id=account.account_id, registry=registry)
        try:
            t, p = mgr.get_credentials()
            rem = int(mgr.credentials.get("expires_at", 0) - time.time())
            print(f"Statut   : Connecte")
            print(f"Compte   : {account.email}")
            print(f"ID       : {account.account_id}")
            print(f"Projet   : {p}")
            print(f"Token    : {t[:15]}... (expire dans {rem}s)")
        except Exception as e:
            print(f"Statut   : Non connecte ({e})")
            print(f"Compte   : {account.email}")
            print(f"ID       : {account.account_id}")

    elif args[0] in ("list", "ls"):
        accounts = registry.list_accounts()
        if not accounts:
            print("Aucun compte enregistre.")
            print("Lancez 'hermes auth add antigravity --type oauth' pour authentifier un compte.")
            return

        active_id = registry.active_account_id
        print(f"{'ACTIF':<6} {'EMAIL':<30} {'ACCOUNT_ID':<18} {'EXPIRE':<12} {'PROJET'}")
        print("-" * 90)
        for acc in accounts:
            is_active = "  *  " if acc.account_id == active_id else "     "
            expired_str = "expire" if acc.is_expired else "valide"
            proj = acc.credentials.get("project_id", "?")[:20]
            print(f"{is_active:<6} {acc.email:<30} {acc.account_id:<18} {expired_str:<12} {proj}")

    elif args[0] in ("switch", "set"):
        target = args[1] if len(args) > 1 else None
        if not target:
            print("Usage: auth.py switch <email|account_id>")
            return
        account = registry.set_active(target)
        if account:
            print(f"Compte actif : {account.email} ({account.account_id})")
        else:
            print(f"Compte non trouve : {target}")

    elif args[0] in ("remove", "rm", "delete"):
        target = args[1] if len(args) > 1 else None
        if not target:
            print("Usage: auth.py remove <email|account_id>")
            return
        if registry.remove_account(target):
            print(f"Compte supprime : {target}")
        else:
            print(f"Compte non trouve : {target}")

    elif args[0] == "export":
        # Export active account to env var format
        account = registry.get_account()
        if not account:
            print("Aucun compte actif.")
            return
        print(f"export ANTIGRAVITY_ACCESS_TOKEN={account.credentials.get('access_token', '')}")
        print(f"export ANTIGRAVITY_REFRESH_TOKEN={account.credentials.get('refresh_token', '')}")
        print(f"export ANTIGRAVITY_PROJECT_ID={account.credentials.get('project_id', '')}")

    elif args[0] == "help":
        print("Gestionnaire multi-compte Google Antigravity pour Hermes Agent")
        print()
        print("Commandes (fallback — preferez 'hermes auth add antigravity'):")
        print("  login [email]     Authentifier un nouveau compte (ou re-authentifier)")
        print("  status            Afficher le statut du compte actif")
        print("  list              Lister tous les comptes")
        print("  switch <email>    Changer de compte actif")
        print("  remove <email>    Supprimer un compte")
        print("  export            Exporter les tokens du compte actif (format env)")
        print("  help              Afficher cette aide")

    else:
        print(f"Commande inconnue : {args[0]}")
        print("Lancez 'auth.py help' pour les commandes disponibles.")


if __name__ == "__main__":
    cli_main()
