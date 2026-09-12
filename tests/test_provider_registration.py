import sys
import pytest

# Ensure Hermes agent is in sys.path dynamically
from pathlib import Path
hermes_agent_dir = Path.home() / ".hermes" / "hermes-agent"
if hermes_agent_dir.is_dir() and str(hermes_agent_dir) not in sys.path:
    sys.path.insert(0, str(hermes_agent_dir))
from providers import get_provider_profile, list_providers


def test_antigravity_profile_registered():
    profile = get_provider_profile("antigravity")
    assert profile is not None
    assert profile.name == "antigravity"
    assert "google-antigravity" in profile.aliases
    assert "agy" in profile.aliases
    assert profile.supports_vision is True
    assert profile.default_aux_model == "gemini-3.8-flash"


def test_antigravity_create_client_hook():
    profile = get_provider_profile("antigravity")
    assert profile is not None
    client = profile.create_client()
    assert client is not None
    assert hasattr(client, "chat")
    assert hasattr(client.chat, "completions")
    assert hasattr(client.chat.completions, "create")

def test_antigravity_model_flow_hook():
    # The plugin owns a callable model flow for `hermes model` integration.
    import importlib.util
    from pathlib import Path
    init_path = Path(__file__).resolve().parent.parent / "__init__.py"
    spec = importlib.util.spec_from_file_location("_ag_init", init_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert callable(getattr(mod, "_model_flow_antigravity", None))
    # Core registration is core-owned: only assert it when the core
    # exposes the flow registry (older cores had _PROVIDER_MODEL_FLOWS;
    # newer cores wire flows natively).
    try:
        import hermes_cli.main as _m
    except ImportError:
        return
    flows = getattr(_m, "_PROVIDER_MODEL_FLOWS", None)
    if flows is None:
        return
    assert "antigravity" in flows
    assert callable(flows["antigravity"])


def test_antigravity_skips_models_health_probe():
    # Google has no REST /models on this base URL (catalog is
    # fetchAvailableModels) — the profile must opt out of the probe.
    profile = get_provider_profile("antigravity")
    assert profile is not None
    assert profile.supports_health_check is False
