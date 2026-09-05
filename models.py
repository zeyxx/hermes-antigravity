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

DEFAULT_USER_AGENT = (
    "antigravity/cli/1.1.23 (aidev_client; os_type=linux; arch=amd64; cl=974125021; auth_method=consumer)"
)


def resolve_runtime_model(model_id: str) -> str:
    """Strip provider prefix and return clean model identifier."""
    clean = model_id.strip()
    if clean.startswith("antigravity/"):
        clean = clean[len("antigravity/") :]
    return clean


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
