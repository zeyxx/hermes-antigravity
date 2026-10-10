"""Model catalog and discovery for Antigravity provider in Hermes Agent."""

from __future__ import annotations

import json
import logging
import os
import platform
import time
import urllib.error
import urllib.request
from typing import Any

try:
    from .accounts import HERMES_ROOT
except ImportError:
    from accounts import HERMES_ROOT
try:
    from . import quota as _quota
except ImportError:
    import quota as _quota

logger = logging.getLogger(__name__)


CLI_VERSION = "1.2.4"
CLI_BUILD = "982146307"


def get_user_agent() -> str:
    """Return authentic Antigravity CLI wire User-Agent matching host OS and arch.

    CLI_VERSION / CLI_BUILD track the official Antigravity desktop client that the
    relay expects. When upstream moves, tests/test_models.py fails until this is
    bumped in lockstep with pi-antigravity.
    """
    sys_os = platform.system().lower()
    os_type = "windows" if "win" in sys_os else ("darwin" if "darwin" in sys_os else "linux")
    machine = platform.machine().lower()
    arch = "arm64" if ("arm" in machine or "aarch64" in machine) else "amd64"
    return (
        f"antigravity/cli/{CLI_VERSION} (aidev_client; os_type={os_type}; "
        f"arch={arch}; cl={CLI_BUILD}; auth_method=consumer)"
    )


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


# ── Model enums ────────────────────────────────────────────────────────────
# The relay's `model_enum` wire label is the value it returns as `info.model` from
# fetchAvailableModels. It is NOT derivable from the model id: gemini-3.8-flash-low
# maps to MODEL_PLACEHOLDER_M320, not to the id with dashes replaced. Synthesising
# one sends a value the relay does not recognise.
#
# Ported from pi-antigravity: a dynamic cache fed by discovery, persisted so a
# cold start keeps the last-known-good values, with a static table for models that
# discovery has not reported yet.

MODEL_ENUM_STORE = HERMES_ROOT / "antigravity-model-enums.json"

_STATIC_MODEL_ENUMS: dict[str, str] = {
    # Gemini 3.8 Flash
    "gemini-3.8-flash": "MODEL_PLACEHOLDER_M318",
    "gemini-3.8-flash-high": "MODEL_PLACEHOLDER_M318",
    "gemini-3.8-flash-medium": "MODEL_PLACEHOLDER_M319",
    "gemini-3.8-flash-low": "MODEL_PLACEHOLDER_M320",
    "gemini-3.8-flash-tiered": "MODEL_PLACEHOLDER_M322",
    # Gemini Pro / agent variants
    "gemini-pro-agent": "MODEL_PLACEHOLDER_M16",
    "gemini-3-flash-agent": "MODEL_PLACEHOLDER_M84",
    # Gemini 3.1 Pro
    "gemini-3.1-pro": "MODEL_PLACEHOLDER_M36",
    "gemini-3.1-pro-high": "MODEL_PLACEHOLDER_M37",
    "gemini-3.1-pro-low": "MODEL_PLACEHOLDER_M36",
    # Claude
    "claude-opus-4-6": "MODEL_PLACEHOLDER_M26",
    "claude-opus-4-6-thinking": "MODEL_PLACEHOLDER_M26",
    "claude-sonnet-4-6": "MODEL_PLACEHOLDER_M35",
    # Gemini 3.7 Flash
    "gemini-3.7-flash": "MODEL_PLACEHOLDER_M298",
    "gemini-3.7-flash-high": "MODEL_PLACEHOLDER_M298",
    "gemini-3.7-flash-medium": "MODEL_PLACEHOLDER_M299",
    "gemini-3.7-flash-low": "MODEL_PLACEHOLDER_M300",
    "gemini-3.7-flash-tiered": "MODEL_PLACEHOLDER_M301",
    # Gemini 3.6 Flash
    "gemini-3.6-flash": "MODEL_PLACEHOLDER_M71",
    "gemini-3.6-flash-high": "MODEL_PLACEHOLDER_M71",
    "gemini-3.6-flash-medium": "MODEL_PLACEHOLDER_M72",
    "gemini-3.6-flash-low": "MODEL_PLACEHOLDER_M73",
    "gemini-3.6-flash-tiered": "MODEL_PLACEHOLDER_M196",
    # Gemini 3.5 Flash
    "gemini-3.5-flash": "MODEL_PLACEHOLDER_M20",
    "gemini-3.5-flash-low": "MODEL_PLACEHOLDER_M20",
    "gemini-3.5-flash-extra-low": "MODEL_PLACEHOLDER_M187",
    # OpenAI open-weight: enum does not follow the MODEL_PLACEHOLDER pattern
    "gpt-oss-120b": "MODEL_OPENAI_GPT_OSS_120B_MEDIUM",
    "gpt-oss-120b-medium": "MODEL_OPENAI_GPT_OSS_120B_MEDIUM",
}

_model_enum_cache: dict[str, str] = {}
_model_enum_loaded = False


def _load_model_enums() -> dict[str, str]:
    """Restore persisted enums once per process; a corrupt store is ignored."""
    global _model_enum_loaded
    if _model_enum_loaded:
        return _model_enum_cache
    _model_enum_loaded = True
    try:
        raw = json.loads(MODEL_ENUM_STORE.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and raw.get("version") == 1:
            enums = raw.get("enums")
            if isinstance(enums, dict):
                _model_enum_cache.update(
                    {k: v for k, v in enums.items() if isinstance(v, str) and v}
                )
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.debug("model enum store unreadable: %s", exc)
    return _model_enum_cache


def _save_model_enums() -> None:
    try:
        MODEL_ENUM_STORE.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "enums": dict(_model_enum_cache)}
        MODEL_ENUM_STORE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.chmod(MODEL_ENUM_STORE, 0o600)
    except Exception as exc:
        # Enums are an optimisation for scoring, not a credential; never fail a
        # request because they could not be cached.
        logger.debug("model enum store not written: %s", exc)


def register_model_enum(wire_model_id: str, model_enum: str) -> None:
    """Record one discovered enum, ahead of the static table."""
    if not wire_model_id or not model_enum:
        return
    cache = _load_model_enums()
    if cache.get(wire_model_id) == model_enum:
        return
    cache[wire_model_id] = model_enum
    _save_model_enums()


def register_discovered_model_enums(models: Any) -> None:
    """Register every ``{wire_id: {"model": enum}}`` entry from a discovery payload."""
    if not isinstance(models, dict):
        return
    for wire_id, info in models.items():
        if isinstance(info, dict):
            enum = info.get("model")
            if isinstance(enum, str) and enum:
                register_model_enum(wire_id, enum)


def get_model_enum(wire_model_id: str) -> str | None:
    """The enum for a wire model id, or None when it is genuinely unknown.

    Returning None matters: the caller must omit the label rather than invent a
    value, which is what the previous id-mangling fallback did.
    """
    direct = _load_model_enums().get(wire_model_id)
    if direct:
        return direct
    return _STATIC_MODEL_ENUMS.get(wire_model_id)


def resolve_session_trajectory(
    messages: list[dict], session_id: str | None = None
) -> dict[str, str]:
    """Stable conversationId/trajectoryId for a session (session affinity).

    Google groups multi-turn usage by session; without stable IDs each
    turn looks like a new session.

    An explicit ``session_id`` is authoritative and takes precedence: it is
    the only seed that actually distinguishes two conversations, since two
    unrelated sessions can open on the same text. This mirrors
    pi-antigravity resolveSessionTrajectory (PR #63). Hermes does not always
    forward a session id to providers, so the first-message seed remains as
    a fallback rather than a replacement.

    The fallback seed is the first message only, so follow-up turns in the
    same process reuse the same trajectory.
    """
    import json as _json

    explicit = str(session_id or "").strip()
    if explicit:
        seed = f"session:{explicit}"
    else:
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
        "antigravity/cli-version": CLI_VERSION,
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
    """Query v1internal:fetchAvailableModels across candidate endpoints.

    Also records each model's enum (the relay's own ``info.model`` value), which is
    the only authoritative source for the ``model_enum`` wire label. The ids alone
    are not enough: the enum is not derivable from the model id, so a port that
    keeps only the keys has to invent a value later.
    """
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
                for m_id, info in models_dict.items():
                    if not isinstance(info, dict):
                        info = {}
                    # Register every enum, before the picker filter: the id decides
                    # what the user sees, the enum is what the relay scores, and a
                    # filtered-out id can still be requested by a routing override.
                    enum = info.get("model")
                    if isinstance(enum, str) and enum:
                        register_model_enum(m_id, enum)
                    if any(prefix in m_id for prefix in ("gemini-", "claude-", "gpt-oss-")):
                        discovered.add(m_id)
                if discovered:
                    break
        except Exception as exc:
            logger.debug("fetchAvailableModels failed on %s: %s", endpoint, exc)
            continue

    if discovered:
        return sorted(discovered)
    # An empty live result is authoritative for the picker.  Returning a
    # baked-in list here makes retired or unavailable models look active.
    # Keep FALLBACK_MODELS for compatibility/tests, but never advertise it as
    # a live catalog when Antigravity cannot confirm availability.
    return []


# ── Quota measurement (imperative shell) ───────────────────────────────────
# The pure parsers live in quota.py. This section is the only place that opens
# a socket for quota: it calls the three Antigravity RPCs that carry usage
# facts and hands each response to a pure parser. No quota arithmetic happens
# here, so the wire shape and the maths can change independently.


def _post_json(
    path: str,
    token: str,
    body: dict[str, Any],
    timeout: float,
    endpoints: list[str] | None = None,
) -> dict[str, Any]:
    """POST one v1internal RPC across candidate endpoints; return parsed JSON.

    Mirrors the failover discipline of fetch_available_models: 403/404/429/5xx
    move to the next candidate rather than raising, because the daily and the
    production hosts do not always agree on availability. Any other status is a
    real error and propagates.
    """
    candidates = endpoints or ENDPOINT_FALLBACKS
    last_error = "no endpoint available"
    for endpoint in candidates:
        url = f"{endpoint}{path}"
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": DEFAULT_USER_AGENT,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError as exc:
                    last_error = f"{endpoint} returned non-JSON: {exc}"
                    continue
                return data if isinstance(data, dict) else {}
        except urllib.error.HTTPError as err:
            body_text = ""
            try:
                body_text = err.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            last_error = f"{endpoint} HTTP {err.code}: {body_text[:200]}"
            if err.code in (403, 404, 429, 500, 502, 503, 504):
                continue
            raise
        except Exception as exc:
            last_error = f"{endpoint}: {exc}"
            continue
    raise RuntimeError(f"{path} failed: {last_error}")


def _post_json_safe(
    path: str,
    token: str,
    body: dict[str, Any],
    timeout: float,
    endpoints: list[str] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Best-effort ``_post_json``: (data, error). Never raises.

    The aggregate quota summary is subscription-gated; a failure there must not
    sink the per-model quota or the tier, which are always available. This is
    the seam that keeps one gated RPC from blocking the whole measurement.
    """
    try:
        return _post_json(path, token, body, timeout, endpoints), None
    except Exception as exc:
        logger.debug("%s unavailable: %s", path, exc)
        return None, str(exc)


def fetch_account_quota(
    token: str,
    project_id: str,
    timeout: float = 8.0,
    endpoints: list[str] | None = None,
    email: str | None = None,
) -> dict[str, Any]:
    """Measure one account's Antigravity quota from the three usage RPCs.

    Sources, all parsed by quota.py:

    - ``v1internal:fetchAvailableModels`` — per-model remainingFraction/resetTime
    - ``v1internal:retrieveUserQuotaSummary`` — aggregate groups (paid only)
    - ``v1internal:loadCodeAssist`` — currentTier/paidTier (the plan label)

    Returns a dict shaped for both the CLI report and the proactive failover in
    client.py: ``models`` rows each carry the quota facts, ``groups`` carries
    the aggregate view, and ``groupError`` records why the aggregate is missing
    rather than letting an empty list masquerade as "no quota". A quota summary
    that 403s on a free tier is the expected path, not an error state.
    """
    models_data, models_err = _post_json_safe(
        "/v1internal:fetchAvailableModels",
        token,
        {"project": project_id},
        timeout,
        endpoints,
    )
    summary_data, summary_err = _post_json_safe(
        "/v1internal:retrieveUserQuotaSummary", token, {}, timeout, endpoints
    )
    assist_data, _assist_err = _post_json_safe(
        "/v1internal:loadCodeAssist",
        token,
        {"metadata": {"ideType": "ANTIGRAVITY", "platform": "PLATFORM_UNSPECIFIED", "pluginType": "GEMINI"}},
        timeout,
        endpoints,
    )

    models = _quota.parse_models_quota(models_data) if models_data else []
    groups = _quota.parse_quota_groups(summary_data) if summary_data else []
    plan = None
    if assist_data:
        plan = _quota.plan_label(assist_data.get("currentTier"), assist_data.get("paidTier"))

    return {
        "email": email,
        "projectId": project_id,
        "plan": plan,
        "groups": groups,
        "groupError": summary_err if (summary_err and not groups) else None,
        "models": models,
        "modelsError": models_err if (models_err and not models) else None,
        "fetchedAt": time.time(),
    }

