from unittest.mock import patch
from models import (
    FALLBACK_MODELS,
    DEFAULT_MODEL,
    MODEL_ROUTING,
    resolve_runtime_model,
    get_thinking_config,
    fetch_available_models,
    clamp_max_tokens,
    MAX_OUTPUT_TOKENS,
    DEFAULT_MAX_OUTPUT_TOKENS,
    stable_uuid,
    resolve_session_trajectory,
    antigravity_request_envelope,
    get_user_agent,
    DEFAULT_USER_AGENT,
    CLI_VERSION,
    CLI_BUILD,
)


def test_wire_fingerprint_matches_official_cli():
    """Pin the CLI wire fingerprint to the value pi-antigravity ships.

    This is a drift detector, not a style assertion. The relay scores
    requests on this fingerprint; when upstream bumps the CLI version or
    build number and this port does not, requests degrade silently rather
    than failing loudly. Bump CLI_VERSION/CLI_BUILD in models.py in the
    same change when pi-antigravity moves (upstream 0.8.1 / PR #63 did).
    """
    assert CLI_VERSION == "1.2.4", (
        "Antigravity CLI version drifted from pi-antigravity; update models.py")
    assert CLI_BUILD == "982146307", (
        "Antigravity CLI build number drifted from pi-antigravity; update models.py")
    ua = DEFAULT_USER_AGENT
    assert ua.startswith("antigravity/cli/1.2.4 (")
    assert "cl=982146307" in ua
    assert "auth_method=consumer" in ua


def test_wire_fingerprint_reports_host_os_and_arch():
    """os_type/arch must follow the host, the rest of the fingerprint is fixed."""
    import platform as _platform

    ua = get_user_agent()
    machine = _platform.machine().lower()
    expected_arch = "arm64" if ("arm" in machine or "aarch64" in machine) else "amd64"
    assert f"arch={expected_arch}" in ua
    assert "os_type=" in ua
    assert "os_type=unknown" not in ua


def test_request_envelope_carries_current_cli_version_label():
    envelope = antigravity_request_envelope(
        wire_model_id="gemini-3.8-flash-low",
        step=1,
        last_step_index="0",
        request_index=0,
        conversation_id="conv-1",
        trajectory_id="traj-1",
    )
    assert envelope["labels"]["antigravity/cli-version"] == CLI_VERSION


def test_fallback_models_contains_expected_architectures():
    assert "gemini-3.8-flash" in FALLBACK_MODELS
    assert "gemini-2.5-pro" in FALLBACK_MODELS
    assert "claude-3-7-sonnet" in FALLBACK_MODELS
    assert DEFAULT_MODEL == "gemini-3.8-flash"


def test_resolve_runtime_model_mapping():
    # Public IDs route to suffixed runtime IDs (quota is attributed per
    # runtime ID; bare public IDs 429 even with quota remaining).
    assert resolve_runtime_model("gemini-3.8-flash") == "gemini-3.8-flash-low"
    assert resolve_runtime_model("antigravity/gemini-3.8-flash") == "gemini-3.8-flash-low"
    assert resolve_runtime_model("gemini-3.8-flash", "high") == "gemini-3.8-flash-high"
    assert resolve_runtime_model("gemini-3.8-flash", "medium") == "gemini-3.8-flash-medium"
    assert resolve_runtime_model("gemini-3.5-flash", "high") == "gemini-3-flash-agent"
    assert resolve_runtime_model("claude-sonnet-4-6") == "claude-sonnet-4-6"
    # Already-suffixed runtime IDs pass through unchanged
    assert resolve_runtime_model("gemini-3.8-flash-low") == "gemini-3.8-flash-low"
    # Unknown models pass through unchanged
    assert resolve_runtime_model("claude-3-7-sonnet") == "claude-3-7-sonnet"
    assert resolve_runtime_model("unknown-model") == "unknown-model"


def test_resolve_runtime_model_effort_normalization():
    assert resolve_runtime_model("gemini-3.8-flash", None) == "gemini-3.8-flash-low"
    assert resolve_runtime_model("gemini-3.8-flash", "minimal") == "gemini-3.8-flash-low"
    assert resolve_runtime_model("gemini-3.8-flash", "xhigh") == "gemini-3.8-flash-high"
    assert resolve_runtime_model("gemini-3.8-flash", "bogus") == "gemini-3.8-flash-low"
    assert set(MODEL_ROUTING["gemini-3.8-flash"].values()) >= {
        "gemini-3.8-flash-low", "gemini-3.8-flash-medium", "gemini-3.8-flash-high",
    }


def test_stable_uuid_deterministic():
    assert stable_uuid("seed") == stable_uuid("seed")
    assert stable_uuid("a") != stable_uuid("b")
    assert len(stable_uuid("x").split("-")) == 5


def test_session_trajectory_stable_per_session():
    msgs = [{"role": "user", "content": "hello world"}]
    first = resolve_session_trajectory(msgs)
    # Follow-up turns reuse the same trajectory (seed = first message)
    second = resolve_session_trajectory(msgs + [{"role": "assistant", "content": "hi"}])
    assert first == second
    assert set(first) == {"conversationId", "trajectoryId"}
    other = resolve_session_trajectory([{"role": "user", "content": "different"}])
    assert other != first


def test_request_envelope_labels():
    env = antigravity_request_envelope(
        wire_model_id="gemini-3.8-flash-low",
        step=2,
        last_step_index="1",
        request_index=1,
        conversation_id="conv-1",
        trajectory_id="traj-1",
    )
    assert env["sessionId"] == "conv-1"
    assert env["requestId"] == "traj-1-1-2"
    assert env["labels"]["antigravity/model"] == "gemini_3.8_flash_low"
    assert "antigravity/provider" not in env["labels"]
    claude_env = antigravity_request_envelope("claude-sonnet-4-6", is_claude=True)
    assert claude_env["labels"]["antigravity/provider"] == "anthropic"


def test_get_thinking_config():
    # Thinking enabled for gemini
    cfg = get_thinking_config("gemini-3.8-flash", reasoning_effort="high")
    assert cfg is not None
    assert "thinkingBudget" in cfg
    assert cfg["thinkingBudget"] > 0

    # Thinking disabled
    cfg_off = get_thinking_config("gemini-3.8-flash", reasoning_effort=None)
    assert cfg_off is None


def test_clamp_max_tokens_none_passthrough():
    assert clamp_max_tokens("gemini-3.8-flash", None) is None


def test_clamp_max_tokens_within_cap():
    assert clamp_max_tokens("gemini-3.8-flash", 1000) == 1000


def test_clamp_max_tokens_claude_ceiling():
    assert clamp_max_tokens("claude-opus-4-6-thinking", 65536) == 64000
    assert clamp_max_tokens("claude-sonnet-4-6", 64000) == 64000
    assert clamp_max_tokens("claude-3-7-sonnet", 64001) == 64000


def test_clamp_max_tokens_gpt_oss_ceiling():
    assert clamp_max_tokens("gpt-oss-120b", 65536) == 32768
    assert clamp_max_tokens("gpt-oss-70b", 32768) == 32768


def test_clamp_max_tokens_default_ceiling():
    assert clamp_max_tokens("gemini-2.5-pro", 65536) == 65535
    assert clamp_max_tokens("gemini-3.8-flash", 65537) == 65536


def test_fetch_available_models_empty_on_failure():
    with patch("urllib.request.urlopen", side_effect=Exception("network down")):
        models = fetch_available_models("fake-token", "fake-project")
        assert models == []


def test_core_client_factory_contract_is_detected():
    """The plugin is useless on a core without ProviderProfile.create_client.

    Regression for #4: older cores ignore the hook, build their own OpenAI
    client, and every turn 404s. The provider still shows up in `hermes model`,
    so the symptom reads as a broken endpoint rather than an old core.
    """
    from providers.base import ProviderProfile
    import __init__ as plugin

    assert plugin._CORE_SUPPORTS_CLIENT_FACTORY == plugin._core_supports_client_factory()
    assert plugin._core_supports_client_factory() == hasattr(
        ProviderProfile, "create_client"
    )


def test_core_gate_is_evaluated_against_the_live_core():
    """The gate reads the core it is loaded into, not a hardcoded assumption.

    Removing the hook must make the same hasattr() expression report False.
    The module-level constant is intentionally a snapshot taken at import,
    so this asserts the expression rather than the constant.
    """
    from providers.base import ProviderProfile
    import __init__ as plugin

    original = ProviderProfile.__dict__.get("create_client")
    try:
        if original is not None:
            del ProviderProfile.create_client
        assert not hasattr(ProviderProfile, "create_client")
        assert plugin._core_supports_client_factory() is False
    finally:
        if original is not None:
            ProviderProfile.create_client = original
    assert plugin._core_supports_client_factory() is True


def test_minimum_core_version_is_declared():
    """The floor is a single named constant so it can be cited, not guessed."""
    import __init__ as plugin

    assert plugin.MIN_CORE_VERSION == "0.20.0"
    parts = plugin.MIN_CORE_VERSION.split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts)
