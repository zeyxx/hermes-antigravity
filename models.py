"""Model catalog and discovery for Antigravity provider in Hermes Agent."""

from __future__ import annotations

import json
import logging
import platform
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


def get_user_agent() -> str:
    """Return authentic Antigravity CLI wire User-Agent matching host OS and arch."""
    sys_os = platform.system().lower()
    os_type = "windows" if "win" in sys_os else ("darwin" if "darwin" in sys_os else "linux")
    machine = platform.machine().lower()
    arch = "arm64" if ("arm" in machine or "aarch64" in machine) else "amd64"
    return f"antigravity/cli/1.1.23 (aidev_client; os_type={os_type}; arch={arch}; cl=974125021; auth_method=consumer)"


DEFAULT_USER_AGENT = get_user_agent()

FALLBACK_MODELS: tuple[str, ...] = (
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "claude-3-7-sonnet",
    "claude-3-5-sonnet",
)

DEFAULT_MODEL = "gemini-3.8-flash"

ENDPOINT_FALLBACKS: list[str] = [
    "https://daily-cloudcode-pa.googleapis.com",
    "https://daily-cloudcode-pa.sandbox.googleapis.com",
    "https://cloudcode-pa.googleapis.com",
]


MAX_OUTPUT_TOKENS: dict[str, int] = {"claude": 64000, "gpt-oss": 32768, "gemini-3.8": 65536}
DEFAULT_MAX_OUTPUT_TOKENS: int = 65535


def clamp_max_tokens(model: str, max_tokens: int | None) -> int | None:
    """Clamp max_tokens to the API ceiling for the given model family."""
    if max_tokens is None:
        return None
    name = model.lower()
    cap = DEFAULT_MAX_OUTPUT_TOKENS
    for key, limit in MAX_OUTPUT_TOKENS.items():
        if key in name:
            cap = limit
            break
    return min(max_tokens, cap)


def resolve_runtime_model(model_id: str, reasoning_effort: str | None = None) -> str:
    """Map a public model ID + thinking effort to a Google runtime model ID.

    Google attributes quota on suffixed runtime IDs (``-low``/``-medium``/
    ``-high``). Sending the bare public ID hits a different bucket and
    returns 429 even when quota remains (measured 2026-09-12: bare
    ``gemini-3.8-flash`` 429s while ``gemini-3.8-flash-low`` succeeds on
    the same token). IDs that are already runtime IDs pass through
    unchanged, as do models without a routing entry.
    """
    clean = model_id.strip()
    if clean.startswith("antigravity/"):
        clean = clean[len("antigravity/"):]
    routing = MODEL_ROUTING.get(clean)
    if not routing:
        return clean
    return routing.get(_normalize_effort(reasoning_effort), routing["off"])


def _normalize_effort(reasoning_effort: str | None) -> str:
    """Normalize a free-form effort string to off/low/medium/high."""
    if not reasoning_effort:
        return "off"
    effort = str(reasoning_effort).strip().lower()
    if effort in ("off", "none", "minimal", "low"):
        return "low" if effort in ("minimal", "low") else "off"
    if effort == "medium":
        return "medium"
    if effort in ("high", "xhigh", "max"):
        return "high"
    return "off"


MODEL_ROUTING: dict[str, dict[str, str]] = {
    "gemini-3.8-flash": {
        "off": "gemini-3.8-flash-low",
        "low": "gemini-3.8-flash-low",
        "medium": "gemini-3.8-flash-medium",
        "high": "gemini-3.8-flash-high",
    },
    "gemini-3.7-flash": {
        "off": "gemini-3.7-flash-low",
        "low": "gemini-3.7-flash-low",
        "medium": "gemini-3.7-flash-medium",
        "high": "gemini-3.7-flash-high",
    },
    "gemini-3.6-flash": {
        "off": "gemini-3.6-flash-low",
        "low": "gemini-3.6-flash-low",
        "medium": "gemini-3.6-flash-medium",
        "high": "gemini-3.6-flash-high",
    },
    "gemini-3.5-flash": {
        "off": "gemini-3.5-flash-extra-low",
        "low": "gemini-3.5-flash-extra-low",
        "medium": "gemini-3.5-flash-low",
        "high": "gemini-3-flash-agent",
    },
    "gemini-3.1-pro": {
        "off": "gemini-3.1-pro-low",
        "low": "gemini-3.1-pro-low",
        "medium": "gemini-3.1-pro-low",
        "high": "gemini-pro-agent",
    },
    "claude-sonnet-4-6": {
        "off": "claude-sonnet-4-6",
        "low": "claude-sonnet-4-6",
        "medium": "claude-sonnet-4-6",
        "high": "claude-sonnet-4-6",
    },
    "claude-opus-4-6": {
        "off": "claude-opus-4-6-thinking",
        "low": "claude-opus-4-6-thinking",
        "medium": "claude-opus-4-6-thinking",
        "high": "claude-opus-4-6-thinking",
    },
}


def stable_uuid(seed: str) -> str:
    """Deterministic RFC 4122 v5 UUID from a seed string."""
    import hashlib

    raw = bytearray(hashlib.sha1(seed.encode()).digest()[:16])
    raw[6] = (raw[6] & 0x0F) | 0x50
    raw[8] = (raw[8] & 0x3F) | 0x80
    hex_ = bytes(raw).hex()
    return f"{hex_[:8]}-{hex_[8:12]}-{hex_[12:16]}-{hex_[16:20]}-{hex_[20:]}"


_session_trajectory_map: dict[str, dict[str, str]] = {}


def resolve_session_trajectory(messages: list[dict]) -> dict[str, str]:
    """Stable conversationId/trajectoryId for a session (session affinity).

    Google groups multi-turn usage by session; without stable IDs each
    turn looks like a new session. The seed is the first message only so
    follow-up turns in the same process reuse the same trajectory.
    """
    import json as _json

    first_msg = messages[0] if messages else {}
    content_seed = ""
    if isinstance(first_msg.get("content"), str):
        content_seed = first_msg["content"][:64]
    elif isinstance(first_msg.get("content"), list) and first_msg["content"]:
        content_seed = _json.dumps(first_msg["content"][0])[:64]
    seed = f"{first_msg.get('role', 'user')}:{content_seed}"
    if seed not in _session_trajectory_map:
        _session_trajectory_map[seed] = {
            "conversationId": stable_uuid(f"antigravity:conv:{seed}"),
            "trajectoryId": stable_uuid(f"antigravity:traj:{seed}"),
        }
        if len(_session_trajectory_map) > 64:
            oldest = next(iter(_session_trajectory_map))
            del _session_trajectory_map[oldest]
    return _session_trajectory_map[seed]


def antigravity_request_envelope(
    wire_model_id: str,
    step: int = 1,
    last_step_index: str = "0",
    request_index: int = 0,
    conversation_id: str = "",
    trajectory_id: str = "",
    is_claude: bool = False,
) -> dict[str, Any]:
    """Build requestId, sessionId and labels for one Antigravity request."""
    model_enum = wire_model_id.replace("-", "_")
    step_str = str(step)
    labels: dict[str, str] = {
        "antigravity/cli-version": "1.1.23",
        "antigravity/model": model_enum,
        "antigravity/step": step_str,
        "antigravity/last-step-index": last_step_index,
    }
    if is_claude:
        labels["antigravity/provider"] = "anthropic"
    return {
        "requestId": f"{trajectory_id}-{request_index}-{step_str}",
        "sessionId": conversation_id,
        "labels": labels,
    }


def get_thinking_config(model: str, reasoning_effort: str | None) -> dict[str, Any] | None:
    """Return thinkingConfig structure if reasoning is enabled for this model."""
    if not reasoning_effort:
        return None

    budgets = {
        "low": 2048,
        "medium": 8192,
        "high": 16384,
        "max": 32768,
    }
    budget = budgets.get(str(reasoning_effort).lower(), 8192)

    # Gemini 2.5/3.x and Claude 3.7 support thinking
    if any(m in model for m in ("gemini-2.5", "gemini-3", "claude-3-7")):
        return {"thinkingBudget": budget}

    return None


def fetch_available_models(
    token: str,
    project_id: str,
    timeout: float = 6.0,
    endpoints: list[str] | None = None,
) -> list[str]:
    """Query v1internal:fetchAvailableModels across candidate endpoints."""
    candidates = endpoints or ENDPOINT_FALLBACKS
    discovered: set[str] = set()

    for endpoint in candidates:
        url = f"{endpoint}/v1internal:fetchAvailableModels"
        req = urllib.request.Request(
            url,
            data=json.dumps({"project": project_id}).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": DEFAULT_USER_AGENT,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models_dict = data.get("models", {})
                for m_id in models_dict.keys():
                    if any(prefix in m_id for prefix in ("gemini-", "claude-", "gpt-oss-")):
                        discovered.add(m_id)
                if discovered:
                    break
        except Exception as exc:
            logger.debug("fetchAvailableModels failed on %s: %s", endpoint, exc)
            continue

    if discovered:
        return sorted(discovered)
    return list(FALLBACK_MODELS)
