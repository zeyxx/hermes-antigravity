"""Google Antigravity inference provider plugin for Hermes Agent."""

from __future__ import annotations

import logging
from typing import Any

from providers import register_provider
from providers.base import ProviderProfile

try:
    from .auth import AntigravityAuthManager
    from .client import AntigravityClient
    from .models import FALLBACK_MODELS, fetch_available_models
except ImportError:
    from auth import AntigravityAuthManager
    from client import AntigravityClient
    from models import FALLBACK_MODELS, fetch_available_models

logger = logging.getLogger(__name__)


class AntigravityProfile(ProviderProfile):
    """Profile for Google Antigravity / Cloud Code Assist."""

    def create_client(self, **client_kwargs: Any) -> Any:
        """Instantiate and return AntigravityClient for Hermes AIAgent."""
        auth_mgr = AntigravityAuthManager()
        return AntigravityClient(auth_manager=auth_mgr)

    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 8.0,
    ) -> list[str]:
        """Fetch available models dynamically from Antigravity API or return fallbacks."""
        auth_mgr = AntigravityAuthManager()
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
    auth_type="api_key",
    supports_vision=True,
    supports_vision_tool_messages=True,
    default_max_tokens=65536,
    default_aux_model="gemini-3.8-flash",
    fallback_models=FALLBACK_MODELS,
)

register_provider(antigravity)

# Auto-populate env var for Hermes runtime resolver if local credentials exist
try:
    import os
    from .auth import load_local_token
    _cached = load_local_token()
    if _cached and _cached.get("access_token") and "ANTIGRAVITY_ACCESS_TOKEN" not in os.environ:
        os.environ["ANTIGRAVITY_ACCESS_TOKEN"] = str(_cached["access_token"])
except Exception:
    pass

def _model_flow_antigravity(config=None, current_model="", args=None):
    """Native model selection & OAuth flow for Google Antigravity in `hermes model`."""
    from .auth import AntigravityAuthManager
    from .models import FALLBACK_MODELS, fetch_available_models
    from hermes_cli.auth import _prompt_model_selection
    from hermes_cli.model_setup_flows_common import _activate_provider_model

    auth_mgr = AntigravityAuthManager()
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
        _activate_provider_model(
            selected,
            "antigravity",
            "https://daily-cloudcode-pa.googleapis.com",
            f"✓ Modèle Google Antigravity configuré : {selected}",
        )

