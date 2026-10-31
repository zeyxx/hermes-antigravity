"""Google Antigravity inference provider plugin for Hermes Agent.

Multi-account support: manages multiple Google accounts with automatic
migration from legacy single-account format.

Native Hermes auth integration: registers with `hermes auth add antigravity`
and `hermes auth remove antigravity` at plugin load time.
"""
from __future__ import annotations

import logging
import sys
from typing import Any

from providers import register_provider
from providers.base import ProviderProfile

try:
    from .accounts import AntigravityAccountRegistry
    from .auth import (
        AntigravityAuthManager,
        antigravity_auth_handler,
        antigravity_refresh_credential,
        resolve_pool_credential,
    )
    from .client import AntigravityClient
    from .models import fetch_available_models
except ImportError:
    from accounts import AntigravityAccountRegistry
    from auth import (
    AntigravityAuthManager,
    antigravity_auth_handler,
    antigravity_refresh_credential,
    resolve_pool_credential,
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


def _resolve_pool_credential(prefer_token: str | None = None) -> Any:
    """Resolve pool credential, honoring patches on either module."""
    for mod_name in ("auth",):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "resolve_pool_credential"):
            try:
                return mod.resolve_pool_credential(prefer_token=prefer_token)
            except TypeError:
                return mod.resolve_pool_credential(prefer_token)
    # Fall back to the name imported into this module (may be patched).
    fn = globals().get("resolve_pool_credential")
    if callable(fn):
        try:
            return fn(prefer_token=prefer_token)
        except TypeError:
            return fn(prefer_token)
    return None


def _registry_account_usable(account: Any) -> bool:
    """True when the registry holds an account with a usable token."""
    if account is None:
        return False
    creds = getattr(account, "credentials", None)
    if not isinstance(creds, dict):
        creds = {}
    return bool(creds.get("access_token") or creds.get("refresh_token"))


class AntigravityProfile(ProviderProfile):
    """Profile for Google Antigravity / Cloud Code Assist."""

    def create_client(self, **client_kwargs: Any) -> Any:
        """Instantiate AntigravityClient, pool row first, registry fallback.

        The core leases the pool bearer into ``api_key``; serving that exact
        row keeps inference on the credential ``auth_handler`` wrote (catalog
        review). Outside Hermes (no pool) the account registry still serves.
        """
        leased = _resolve_pool_credential(
            prefer_token=client_kwargs.get("api_key")
            if isinstance(client_kwargs.get("api_key"), str) else None,
        )
        if leased is not None:
            return AntigravityClient(
                auth_manager=AntigravityAuthManager(credentials=leased))
        # If the pool was cleared (e.g. logout) and this registry was pool-managed,
        # drop stale tokens so logout actually disconnects. Skip under tests.
        import os as _os
        if not _os.environ.get("PYTEST_CURRENT_TEST"):
            try:
                from agent.credential_pool import load_pool

                registry = AntigravityAccountRegistry()
                if (not load_pool("antigravity").entries()
                        and registry._data.get("_pool_managed")):
                    registry._data = {"version": 2, "active_account": None, "accounts": {}, "_pool_managed": True}
                    registry._save()
            except Exception:
                pass
        registry = AntigravityAccountRegistry()
        account = registry.get_account()
        if not _registry_account_usable(account):
            raise RuntimeError(
                "Antigravity is not authenticated. Run `hermes auth add antigravity` "
                "to sign in; inference never starts an interactive login."
            )
        auth_mgr = AntigravityAuthManager(registry=registry)
        return AntigravityClient(auth_manager=auth_mgr)

    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 8.0,
    ) -> list[str]:
        """Fetch only models confirmed by the live Antigravity catalog.

        ``api_key`` (the pool bearer the core passes) wins; otherwise lease
        the pool's selected row; the registry is the standalone fallback.
        """
        del base_url
        leased = _resolve_pool_credential(
            prefer_token=api_key if isinstance(api_key, str) else None)
        if leased is not None:
            try:
                return fetch_available_models(
                    leased["access_token"], leased["project_id"], timeout=timeout)
            except Exception as exc:
                logger.debug("fetch_models failed: %s", exc)
                return []
        import os as _os
        if not _os.environ.get("PYTEST_CURRENT_TEST"):
            try:
                from agent.credential_pool import load_pool

                registry = AntigravityAccountRegistry()
                if (not load_pool("antigravity").entries()
                        and registry._data.get("_pool_managed")):
                    registry._data = {"version": 2, "active_account": None, "accounts": {}, "_pool_managed": True}
                    registry._save()
            except Exception:
                pass
        registry = AntigravityAccountRegistry()
        account = registry.get_account()
        if not _registry_account_usable(account):
            logger.debug(
                "fetch_models: no usable account; run `hermes auth add antigravity`")
            return []
        auth_mgr = AntigravityAuthManager(registry=registry)
        try:
            token, project_id = auth_mgr.get_credentials()
            return fetch_available_models(token, project_id, timeout=timeout)
        except Exception as exc:
            logger.debug("fetch_models failed: %s", exc)
            return []


def _profile_kwargs() -> dict[str, Any]:
    """Keyword args for AntigravityProfile, compatible with old and new cores.

    ``env_sources_last`` only exists on cores with the pool-priority hook;
    older cores ignore pool order via ``getattr`` defaults, so omitting the
    flag there degrades to default ordering instead of refusing to load.
    """
    kwargs: dict[str, Any] = {}
    try:
        from dataclasses import fields as _fields

        names = {f.name for f in _fields(ProviderProfile)}
    except Exception:
        names = set()
    if "env_sources_last" in names:
        kwargs["env_sources_last"] = True
    return kwargs


antigravity = AntigravityProfile(
    name="antigravity",
    aliases=("google-antigravity", "agy", "cloudcode", "google-code-assist", "gemini-oauth", "google-oauth"),
    display_name="Google Antigravity",
    description="Google Antigravity / Cloud Code Assist (Gemini 2.5/3.x, Claude Sonnet)",
    signup_url="https://antigravity.google/",
    # Every name here is read by the code (auth.py env fallback, OAuth client
    # override): declaring exactly the read set keeps the manifest honest for
    # `hermes plugins capabilities` and the catalog entry (rule 5).
    env_vars=(
        "ANTIGRAVITY_CLIENT_ID",
        "ANTIGRAVITY_CLIENT_SECRET",
    ),
    base_url="https://daily-cloudcode-pa.googleapis.com",
    # NOTE: declared as api_key (not oauth_external) so the core's plugin
    # bridge (_register_plugin_provider, api_key/external_process only)
    # admits this profile into PROVIDER_REGISTRY and the CLI credential
    # gate. The injected credential is the pool bearer the core leases
    # (see create_client/fetch_models pool-first); a dedicated
    # auth_type="oauth_external" hook is requested in the core issue tracker.
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
    # ANTIGRAVITY_ACCESS_TOKEN / ANTIGRAVITY_PROJECT_ID are direct-API fallbacks: provider-owned
    # OAuth accounts must be selected before them (on cores without the
    # pool-priority hook this is default pool order; see _profile_kwargs).
    **_profile_kwargs(),
)

register_provider(antigravity)

logger.debug("Antigravity auth surfaces declared on the provider profile")

