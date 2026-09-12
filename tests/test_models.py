from unittest.mock import patch
from models import (
    FALLBACK_MODELS,
    DEFAULT_MODEL,
    resolve_runtime_model,
    get_thinking_config,
    fetch_available_models,
    clamp_max_tokens,
    MAX_OUTPUT_TOKENS,
    DEFAULT_MAX_OUTPUT_TOKENS,
)


def test_fallback_models_contains_expected_architectures():
    assert "gemini-3.8-flash" in FALLBACK_MODELS
    assert "gemini-2.5-pro" in FALLBACK_MODELS
    assert "claude-3-7-sonnet" in FALLBACK_MODELS
    assert DEFAULT_MODEL == "gemini-3.8-flash"


def test_resolve_runtime_model_mapping():
    # Canonical IDs
    assert resolve_runtime_model("gemini-3.8-flash") == "gemini-3.8-flash"
    assert resolve_runtime_model("antigravity/gemini-3.8-flash") == "gemini-3.8-flash"
    assert resolve_runtime_model("claude-3-7-sonnet") == "claude-3-7-sonnet"
    assert resolve_runtime_model("unknown-model") == "unknown-model"


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


def test_fetch_available_models_fallback_on_failure():
    with patch("urllib.request.urlopen", side_effect=Exception("network down")):
        models = fetch_available_models("fake-token", "fake-project")
        assert models == list(FALLBACK_MODELS)
