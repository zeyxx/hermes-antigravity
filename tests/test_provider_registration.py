import sys

# Ensure Hermes agent is in sys.path dynamically
from pathlib import Path
hermes_agent_dir = Path.home() / ".hermes" / "hermes-agent"
if hermes_agent_dir.is_dir() and str(hermes_agent_dir) not in sys.path:
    sys.path.insert(0, str(hermes_agent_dir))
from providers import get_provider_profile  # noqa: E402


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
    import hermes_cli.main as _m
    assert "antigravity" in _m._PROVIDER_MODEL_FLOWS
    assert callable(_m._PROVIDER_MODEL_FLOWS["antigravity"])
