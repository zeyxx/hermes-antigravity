"""Google Antigravity inference provider plugin for Hermes Agent.

Multi-account support: manages multiple Google accounts with automatic
migration from legacy single-account format.

Native Hermes auth integration: registers with `hermes auth add antigravity`
and `hermes auth remove antigravity` at plugin load time.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from providers import register_provider
from providers.base import ProviderProfile

try:
    from .accounts import AntigravityAccountRegistry
    from .auth import AntigravityAuthManager, register_hermes_auth
    from .client import AntigravityClient
    from .models import FALLBACK_MODELS, fetch_available_models
except ImportError:
    from accounts import AntigravityAccountRegistry
    from auth import AntigravityAuthManager, register_hermes_auth
    from client import AntigravityClient
    from models import FALLBACK_MODELS, fetch_available_models

logger = logging.getLogger(__name__)


class AntigravityProfile(ProviderProfile):
    """Profile for Google Antigravity / Cloud Code Assist."""

    def create_client(self, **client_kwargs: Any) -> Any:
        """Instantiate and return AntigravityClient for Hermes AIAgent."""
        registry = AntigravityAccountRegistry()
        auth_mgr = AntigravityAuthManager(registry=registry)
        return AntigravityClient(auth_manager=auth_mgr)

    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 8.0,
    ) -> list[str]:
        """Fetch available models dynamically from Antigravity API or return fallbacks."""
        registry = AntigravityAccountRegistry()
        auth_mgr = AntigravityAuthManager(registry=registry)
        try:
            token, project_id = auth_mgr.get_credentials()
            return fetch_available_models(token, project_id, timeout=timeout)
        except Exception as exc:
            logger.debug("fetch_models failed: %s", exc)
            return list(FALLBACK_MODELS)


antigravity = AntigravityProfile(
    name="antigravity",
    aliases=("google-antigravity", "agy", "cloudcode", "google-code-assist", "gemini-oauth", "google-oauth"),
    display_name="Google Antigravity",
    description="Google Antigravity / Cloud Code Assist (Gemini 2.5/3.x, Claude Sonnet)",
    signup_url="https://antigravity.google/",
    env_vars=("ANTIGRAVITY_ACCESS_TOKEN", "ANTIGRAVITY_API_KEY", "GOOGLE_OAUTH_TOKEN"),
    base_url="https://daily-cloudcode-pa.googleapis.com",
    # NOTE: declared as api_key (not oauth_external) so the core's plugin
    # bridge (_register_plugin_provider, api_key/external_process only)
    # admits this profile into PROVIDER_REGISTRY and the CLI credential
    # gate. This is truthful at the wire layer: the injected credential is
    # a bearer token from ANTIGRAVITY_ACCESS_TOKEN (populated from the
    # OAuth registry at import). The OAuth truth lives in the overlay,
    # _oauth_auth_type and register_hermes_auth() below.
    auth_type="api_key",
    supports_vision=True,
    supports_vision_tool_messages=True,
    default_max_tokens=65536,
    default_aux_model="gemini-3.8-flash",
    fallback_models=FALLBACK_MODELS,
)

# Declare true auth type for hermes auth registration (OAuth2 PKCE, not API key)
antigravity._oauth_auth_type = "oauth_external"

register_provider(antigravity)

# Auto-populate env var for Hermes runtime resolver if local credentials exist
try:
    _reg = AntigravityAccountRegistry()
    _active = _reg.get_account()
    if _active and _active.credentials.get("access_token") and "ANTIGRAVITY_ACCESS_TOKEN" not in os.environ:
        os.environ["ANTIGRAVITY_ACCESS_TOKEN"] = str(_active.credentials["access_token"])
except Exception:
    pass

# Register with hermes auth system (enables `hermes auth add antigravity`)
_hermes_auth_registered = register_hermes_auth()
if _hermes_auth_registered:
    logger.debug("Antigravity registered with hermes auth system")
else:
    logger.debug("Antigravity hermes auth registration skipped (hermes_cli unavailable)")

# Register overlay so Hermes main chat uses plugin's AntigravityClient
try:
    from hermes_cli.providers import HERMES_OVERLAYS, HermesOverlay
    HERMES_OVERLAYS["antigravity"] = HermesOverlay(
        auth_type="oauth_external",
        base_url_override="https://daily-cloudcode-pa.googleapis.com",
    )
    logger.debug("Antigravity overlay registered")
except Exception as exc:
    logger.debug("Antigravity overlay registration skipped: %s", exc)


def _model_flow_antigravity(config=None, current_model="", args=None):
    """Native model selection & OAuth flow for Google Antigravity in `hermes model`."""
    from .auth import AntigravityAuthManager
    from .models import FALLBACK_MODELS, fetch_available_models
    from hermes_cli.auth import _prompt_model_selection
    from hermes_cli.model_setup_flows_common import _activate_provider_model

    registry = AntigravityAccountRegistry()

    # If multiple accounts, prompt to select one
    accounts = registry.list_accounts()
    if len(accounts) > 1:
        print("\n=== Multi-compte Antigravity detecte ===")
        for i, acc in enumerate(accounts, 1):
            active = " (actif)" if acc.account_id == registry.active_account_id else ""
            print(f"  {i}. {acc.email}{active}")
        print(f"  {len(accounts) + 1}. Ajouter un nouveau compte")
        print()
        choice = input("Selectionnez un compte (numero) : ").strip()
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(accounts):
                registry.set_active(accounts[idx].account_id)
            elif idx == len(accounts):
                # Add new account
                auth_mgr = AntigravityAuthManager(registry=registry)
                auth_mgr.login_interactive()
        except (ValueError, IndexError):
            pass

    auth_mgr = AntigravityAuthManager(registry=registry)
    token, project_id = auth_mgr.get_credentials()
    models = fetch_available_models(token, project_id) or list(FALLBACK_MODELS)
    default = current_model if current_model in models else models[0]
    selected = _prompt_model_selection(
        models,
        current_model=default,
        confirm_provider="antigravity",
        confirm_base_url="https://daily-cloudcode-pa.googleapis.com",
    )
    if selected:
        msg = f"Modele Google Antigravity configure : {selected}"
        if auth_mgr.email:
            msg += f" (compte: {auth_mgr.email})"
        _activate_provider_model(
            selected,
            "antigravity",
            "https://daily-cloudcode-pa.googleapis.com",
            msg,
        )


# Best-effort: expose the flow to `hermes model` when the core exposes a
# mutable flow registry. Core-owned, guarded — absence only means the
# picker falls back to the generic provider path.
try:
    from hermes_cli.main import _PROVIDER_MODEL_FLOWS as _core_flows
    if isinstance(_core_flows, dict):
        _core_flows.setdefault("antigravity", _model_flow_antigravity)
        logger.debug("Antigravity model flow registered with hermes core")
except Exception as exc:
    logger.debug("Antigravity model flow core registration skipped: %s", exc)
