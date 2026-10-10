"""Quota measurement for Google Antigravity accounts in Hermes Agent.

Ported from Rahul Arya's pi-antigravity (https://github.com/Rahularya01/pi-antigravity,
MIT License), src/usage/usage.ts and src/types/types.ts.

Google already returns per-model quota on the endpoint this plugin calls for
discovery (``v1internal:fetchAvailableModels``). Before this module the data was
parsed and dropped: only the model ids and enums were kept, so the plugin could
not see remaining quota until a request failed with HTTP 429. That is a reactive
wall, not a measurement.

This module is the Functional Core: pure parsing and formatting of quota facts,
zero I/O. The network calls live in ``fetch_account_quota`` (models.py seam),
which feeds the parsed results here. Keeping the split this way means quota
arithmetic (fraction clamping, percent rounding, reset-time deltas, plan-label
precedence) is unit-testable without a single mocked socket.

Wire note: ``remainingFraction`` is a float in [0, 1]. It is pool-shared, not a
private per-model budget — several runtime ids can draw on the same bucket, so a
low fraction on one row does not mean that model alone is nearly exhausted.
"""
from __future__ import annotations

import math
from typing import Any

logger = __import__("logging").getLogger(__name__)


# ── Pure parsing ───────────────────────────────────────────────────────────


def clamp_fraction(value: Any) -> float | None:
    """Coerce a wire ``remainingFraction`` to a float in [0, 1], or None.

    The relay returns the fraction as a JSON number, but upstream also sees
    string-shaped values on some accounts. A value that is not finite, not a
    number, or out of range yields None rather than a fabricated 0 — an unknown
    quota stays unknown instead of reading as "exhausted".
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return None
    if not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    if value < 0:
        return 0.0
    if value > 1:
        return 1.0
    return float(value)


def remaining_percent(remaining: float | None) -> float | None:
    """Fraction to a one-decimal percentage (0.375 -> 37.5)."""
    if remaining is None:
        return None
    return round(remaining * 1000) / 10


def _as_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _as_bool(value: Any) -> bool:
    return value is True or value == "true"


def parse_quota_buckets(group: Any) -> list[dict[str, Any]]:
    """Parse one quota group's buckets from ``retrieveUserQuotaSummary``.

    A bucket needs either a remaining fraction or a bucket id to be a real
    limit; a row with neither is skipped rather than shown as 0% left.
    """
    if not isinstance(group, dict):
        return []
    buckets: list[dict[str, Any]] = []
    raw_buckets = group.get("buckets")
    if not isinstance(raw_buckets, list):
        return []
    for raw in raw_buckets:
        if not isinstance(raw, dict):
            continue
        remaining = clamp_fraction(raw.get("remainingFraction"))
        bucket_id = _as_str(raw.get("bucketId"))
        if remaining is None and not bucket_id:
            continue
        buckets.append({
            "bucketId": bucket_id or _as_str(raw.get("displayName")) or "unknown",
            "displayName": _as_str(raw.get("displayName")) or bucket_id or "Limit",
            "window": _as_str(raw.get("window")),
            "resetTime": _as_str(raw.get("resetTime")),
            "description": _as_str(raw.get("description")),
            "remainingFraction": remaining if remaining is not None else 0.0,
        })
    return buckets


def parse_quota_groups(data: Any) -> list[dict[str, Any]]:
    """Parse the aggregate quota summary into groups of buckets.

    The endpoint is subscription-gated: free-tier accounts get HTTP 403
    SUBSCRIPTION_REQUIRED, so an empty result here is expected there and must
    not be read as "no quota". The caller reports the error separately.
    """
    if not isinstance(data, dict):
        return []
    raw_groups = data.get("groups")
    if not isinstance(raw_groups, list):
        return []
    groups: list[dict[str, Any]] = []
    for raw_group in raw_groups:
        buckets = parse_quota_buckets(raw_group)
        display_name = _as_str(raw_group.get("displayName")) if isinstance(raw_group, dict) else None
        if not buckets and not display_name:
            continue
        groups.append({
            "displayName": display_name or "Quota group",
            "description": _as_str(raw_group.get("description")) if isinstance(raw_group, dict) else None,
            "buckets": buckets,
        })
    return groups


def parse_model_quota(model_id: str, info: Any) -> dict[str, Any] | None:
    """Extract the quota view of one model from a ``fetchAvailableModels`` entry.

    Returns None for internal/chat-prefixed rows, which the relay advertises but
    no client requests directly.
    """
    if not isinstance(info, dict):
        return None
    if info.get("isInternal") or model_id.startswith("chat_"):
        return None
    quota_info = info.get("quotaInfo")
    quota_info = quota_info if isinstance(quota_info, dict) else {}
    display_name = _as_str(info.get("displayName"))
    label = _as_str(info.get("label"))
    model_name = _as_str(info.get("modelName"))
    return {
        "modelId": model_id,
        "displayName": display_name or label or model_name,
        "remainingFraction": clamp_fraction(quota_info.get("remainingFraction")),
        "resetTime": _as_str(quota_info.get("resetTime")),
        "isExhausted": _as_bool(quota_info.get("isExhausted")),
        "supportsThinking": _as_bool(info.get("supportsThinking")),
        "supportsImages": _as_bool(info.get("supportsImages")),
        "recommended": _as_bool(info.get("recommended")),
    }


def parse_models_quota(data: Any) -> list[dict[str, Any]]:
    """Every non-internal model row with its quota, sorted by model id."""
    if not isinstance(data, dict):
        return []
    models = data.get("models")
    if not isinstance(models, dict):
        return []
    rows: list[dict[str, Any]] = []
    for model_id, info in models.items():
        row = parse_model_quota(str(model_id), info)
        if row is not None:
            rows.append(row)
    rows.sort(key=lambda r: r["modelId"])
    return rows


def parse_tier(value: Any) -> dict[str, Any] | None:
    """Parse a tier object (currentTier / paidTier) from loadCodeAssist."""
    if not isinstance(value, dict):
        return None
    tier_id = _as_str(value.get("id"))
    name = _as_str(value.get("name"))
    if not tier_id and not name:
        return None
    return {
        "id": tier_id,
        "name": name,
        "description": _as_str(value.get("description")),
    }


def plan_label(product_tier: Any, paid_tier: Any) -> str | None:
    """Human label for the account's plan.

    Google reports ``currentTier=free-tier`` even on paid AI Pro accounts; the
    real subscription lives in ``paidTier`` (e.g. ``g1-pro-tier``). The paid tier
    wins when present, so a Pro account is not labelled "free".
    """
    paid = parse_tier(paid_tier)
    if paid and paid.get("name"):
        return f"{paid['name']}" + (f" ({paid['id']})" if paid.get("id") else "")
    product = parse_tier(product_tier)
    if product and product.get("name"):
        return f"{product['name']}" + (f" ({product['id']})" if product.get("id") else "")
    return None


# ── Reset-time formatting (pure) ───────────────────────────────────────────


def format_reset(reset_time: str | None, now: float) -> str:
    """Human delta until reset: ``3h 12m``, ``2d 4h``, ``now``, or the raw value.

    An unparseable timestamp is echoed verbatim rather than hidden — the raw
    value is still data. ``now`` for a reset already past, so a stale snapshot
    does not read as a future event.
    """
    if not reset_time:
        return "n/a"
    from datetime import datetime, timezone

    try:
        clean = reset_time.strip()
        if clean.endswith("Z"):
            clean = clean[:-1] + "+00:00"
        parsed = datetime.fromisoformat(clean)
    except ValueError:
        return reset_time
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    delta = parsed.timestamp() - now
    if delta <= 0:
        return "now"
    total_min = round(delta / 60)
    days = total_min // (60 * 24)
    hours = (total_min % (60 * 24)) // 60
    mins = total_min % 60
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def progress_bar(remaining: float | None, width: int = 20) -> str:
    """A fixed-width ASCII bar; ``?`` fill when the fraction is unknown."""
    if remaining is None:
        return "[" + "?" * width + "]"
    filled = max(0, min(width, round(remaining * width)))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


# ── Selection rule (pure) ──────────────────────────────────────────────────


def best_model_row(
    rows: list[dict[str, Any]], model_id: str
) -> dict[str, Any] | None:
    """The quota row for ``model_id``.

    An exact runtime-id match is authoritative and returned as-is: the caller
    routed to that specific id, so its bucket is the one that will be charged.
    Only when no exact row exists does this fall back to ids prefixed with the
    request (``gemini-3.8-flash`` -> ``gemini-3.8-flash-low``), where several
    runtime siblings share a bucket and the one with the most quota is the
    informative pick. Unknown stays None rather than defaulting to 0.
    """
    for row in rows:
        if row.get("modelId") == model_id:
            return row
    prefixed = [
        r for r in rows
        if str(r.get("modelId", "")).startswith(model_id + "-")
    ]
    if not prefixed:
        return None

    def _key(row: dict[str, Any]) -> float:
        value = row.get("remainingFraction")
        return value if isinstance(value, float) else -1.0

    return max(prefixed, key=_key)


def quota_is_low(row: dict[str, Any] | None, threshold: float = 0.0) -> bool:
    """True when this model's bucket is too empty to be worth a request.

    ``threshold`` is a remaining fraction, not a percentage: 0.0 switches only
    on a bucket the relay reports as empty, which is the conservative default —
    a low-but-nonzero bucket may still serve a request, and the existing 429
    backstop catches the case where it cannot. An operator who wants to bail
    earlier raises it (e.g. 0.05 to switch below 5% left).

    An unknown row (None, or a missing fraction) is never "low": a quota we
    cannot read must not be treated as an exhausted one.
    """
    if row is None:
        return False
    if row.get("isExhausted") is True:
        return True
    remaining = row.get("remainingFraction")
    if remaining is None:
        return False
    if isinstance(remaining, bool) or not isinstance(remaining, (int, float)):
        return False
    return float(remaining) <= threshold


# ── Formatting ─────────────────────────────────────────────────────────────


def _pct(value: float | None) -> str:
    percent = remaining_percent(value)
    return "?" if percent is None else f"{percent}%"


def percent_label(value: float | None) -> str:
    """Public wrapper for the ``NN.N%``/``?`` label used by status output."""
    return _pct(value)


def format_quota_report(
    account_email: str | None,
    plan: str | None,
    groups: list[dict[str, Any]],
    models: list[dict[str, Any]],
    group_error: str | None,
    now: float,
) -> str:
    """The operator-facing quota report (also the ``auth.py quota`` output)."""
    import time as _time

    lines: list[str] = []
    lines.append("Antigravity quota")
    if account_email:
        lines.append(f"Account: {account_email}")
    if plan:
        lines.append(f"Plan   : {plan}")
    lines.append("")

    if group_error:
        lines.append(_group_error_note(group_error))
        lines.append("")

    if groups:
        for group in groups:
            lines.append(group["displayName"])
            for bucket in group["buckets"]:
                remaining = bucket.get("remainingFraction")
                lines.append(
                    f"  {progress_bar(remaining)} {bucket['displayName']}: "
                    f"{_pct(remaining)} left · resets {format_reset(bucket.get('resetTime'), now)}"
                )
            lines.append("")
    else:
        lines.append("No aggregate quota groups returned.")
        lines.append("")

    if models:
        lines.append("Per-model remaining (pool-shared, not a private budget):")
        for row in models:
            flags = [
                "recommended" if row.get("recommended") else "",
                "thinking" if row.get("supportsThinking") else "",
                "images" if row.get("supportsImages") else "",
                "exhausted" if row.get("isExhausted") else "",
            ]
            flag_str = ",".join(f for f in flags if f)
            name = f"  {row['displayName']}" if row.get("displayName") and row["displayName"] != row["modelId"] else ""
            reset = format_reset(row.get("resetTime"), now)
            lines.append(
                f"  {row['modelId']:<34} rem {_pct(row.get('remainingFraction')):>6}  "
                f"reset {reset:<8}{('  [' + flag_str + ']') if flag_str else ''}{name}"
            )
    else:
        lines.append("No per-model quota returned.")

    lines.append("")
    lines.append(f"Fetched: {_time.strftime('%Y-%m-%d %H:%M:%S', _time.localtime(now))}")
    return "\n".join(lines).rstrip()


def _group_error_note(msg: str) -> str:
    """Explain a failed aggregate summary without hiding the error."""
    lowered = msg.lower()
    if "subscription_required" in lowered or "3501" in lowered or "license" in lowered:
        return (
            "Aggregate quota summary needs a paid subscription "
            "(free-tier gets 403 SUBSCRIPTION_REQUIRED). "
            "Per-model quota below is still live."
        )
    return f"Aggregate quota summary unavailable: {msg[:160]}"
