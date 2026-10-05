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
    from .auth import (
        AntigravityAuthManager,
        antigravity_auth_handler,
        antigravity_refresh_credential,
    )
    from .client import AntigravityClient
    from .models import fetch_available_models
except ImportError:
    from accounts import AntigravityAccountRegistry
    from auth import (
    AntigravityAuthManager,
    antigravity_auth_handler,
    antigravity_refresh_credential,
)
    from client import AntigravityClient
    from models import fetch_available_models

logger = logging.getLogger(__name__)

# The plugin delivers inference through ProviderProfile.create_client(). Without that
# hook the core builds its own OpenAI-shaped client and POSTs it to
# daily-cloudcode-pa.googleapis.com, which answers 404 for every turn: the provider
# still appears in `hermes model`, so the failure looks like a broken endpoint rather
# than an incompatible core. See https://github.com/zeyxx/hermes-antigravity/issues/4
MIN_CORE_VERSION = "0.20.0"


def _core_supports_client_factory() -> bool:
    """True when this Hermes core routes inference through ProviderProfile.create_client."""
    return hasattr(ProviderProfile, "create_client")


# Snapshot taken at import time; the gate fires once, when the plugin loads.
_CORE_SUPPORTS_CLIENT_FACTORY = _core_supports_client_factory()
if not _CORE_SUPPORTS_CLIENT_FACTORY:
    logger.error(
        "hermes-antigravity requires Hermes core with ProviderProfile.create_client "
        "(newer than v0.19.0, i.e. >= v%s). Please upgrade Hermes Agent."
        % MIN_CORE_VERSION
    )


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
        """Fetch only models confirmed by the live Antigravity catalog."""
        registry = AntigravityAccountRegistry()
        auth_mgr = AntigravityAuthManager(registry=registry)
        try:
            token, project_id = auth_mgr.get_credentials()
            return fetch_available_models(token, project_id, timeout=timeout)
        except Exception as exc:
            logger.debug("fetch_models failed: %s", exc)
            return []


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
    # Google exposes no REST /models here (catalog is fetchAvailableModels);
    # opt out of the core's /models health probe instead of 404ing in doctor.
    supports_health_check=False,
    supports_vision=True,
    supports_vision_tool_messages=True,
    default_max_tokens=65536,
    default_aux_model="gemini-3.8-flash",
    # Antigravity's live catalog is authoritative.  Do not merge stale static
    # IDs into the picker when discovery is unavailable or changes upstream.
    fallback_models=(),
    # Public surface for `hermes auth add|status|logout|refresh antigravity`.
    # auth_handler owns `add` (the PKCE login has no core token endpoint);
    # status and logout are served from the credential pool it fills, and
    # refresh_credential lets the pool rotate the token pair on its own
    # schedule. Declaring these is what keeps the plugin off the core's private
    # tables, which catalog admission rule 9 refuses.
    auth_handler=antigravity_auth_handler,
    refresh_credential=antigravity_refresh_credential,
)

register_provider(antigravity)

# Auto-populate env var for Hermes runtime resolver if local credentials exist
try:
    _reg = AntigravityAccountRegistry()
    _active = _reg.get_account()
    if _active and _active.credentials.get("access_token") and "ANTIGRAVITY_ACCESS_TOKEN" not in os.environ:
        os.environ["ANTIGRAVITY_ACCESS_TOKEN"] = str(_active.credentials["access_token"])
except Exception:
    pass

logger.debug("Antigravity auth surfaces declared on the provider profile")

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
    from hermes_cli.auth import _prompt_model_selection
    from hermes_cli.model_setup_flows_common import _activate_provider_model

    from .auth import AntigravityAuthManager
    from .models import fetch_available_models

    registry = AntigravityAccountRegistry()
    accounts = registry.list_accounts()

    # Always offer the account selector, even with a single account: it is the only
    # way to add another one from this flow. With zero accounts, go straight to login.
    if accounts:
        print("\n=== Comptes Antigravity ===")
        for i, acc in enumerate(accounts, 1):
            active = " (actif)" if acc.account_id == registry.active_account_id else ""
            print(f"  {i}. {acc.email}{active}")
        print(f"  {len(accounts) + 1}. Ajouter un nouveau compte")
        print()
        try:
            choice = input("Selectionnez un compte (numero) : ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAnnulation.")
            return
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(accounts):
                registry.set_active(accounts[idx].account_id)
            elif idx == len(accounts):
                AntigravityAuthManager(registry=registry).login_interactive()
            else:
                print("Entree invalide.")
                return
        except (ValueError, IndexError):
            print("Entree invalide.")
            return
    else:
        print("\n=== Authentification Antigravity ===")
        try:
            AntigravityAuthManager(registry=registry).login_interactive()
        except (EOFError, KeyboardInterrupt):
            print("\nAnnulation.")
            return

    auth_mgr = AntigravityAuthManager(registry=registry)
    try:
        token, project_id = auth_mgr.get_credentials()
    except Exception as exc:
        print(f"Echec de recuperation des credentials: {exc}")
        return
    models = fetch_available_models(token, project_id)
    if not models:
        # The live catalog is authoritative (see #8): an empty result means discovery
        # failed, so do not offer stale ids that would 404 on use.
        print("No active Antigravity models were returned; refresh after the provider is reachable.")
        return
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
