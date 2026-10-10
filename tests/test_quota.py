"""Tests for quota.py (pure core) and the fetch_account_quota seam (models.py).

The parsers are tested without a socket: every wire shape Google can return is
fed in as a dict and checked against the exact parsed fact. The seam test mocks
``urllib.request.urlopen`` the way the other modules do and asserts that the
three RPCs are aggregated, that a gated quota-summary 403 does not sink the
per-model quota, and that quotaInfo is no longer discarded.
"""
import json as _json
from pathlib import Path
from unittest.mock import patch

from quota import (
    clamp_fraction,
    remaining_percent,
    parse_quota_groups,
    parse_model_quota,
    parse_models_quota,
    parse_tier,
    plan_label,
    format_reset,
    progress_bar,
    best_model_row,
    quota_is_low,
    format_quota_report,
)


# ── clamp_fraction ─────────────────────────────────────────────────────────

def test_clamp_fraction_accepts_the_unit_range():
    assert clamp_fraction(0.0) == 0.0
    assert clamp_fraction(1.0) == 1.0
    assert clamp_fraction(0.375) == 0.375


def test_clamp_fraction_clamps_out_of_range():
    assert clamp_fraction(-0.5) == 0.0
    assert clamp_fraction(2.0) == 1.0


def test_clamp_fraction_parses_string_shaped_fractions():
    """Some accounts return the fraction as a string; it must still parse."""
    assert clamp_fraction("0.5") == 0.5


def test_clamp_fraction_unknown_stays_none():
    """A missing quota must read as unknown, never as 'exhausted' (0)."""
    assert clamp_fraction(None) is None
    assert clamp_fraction("n/a") is None
    assert clamp_fraction(True) is None  # bool is not a fraction
    assert clamp_fraction(float("nan")) is None


def test_remaining_percent_rounds_one_decimal():
    assert remaining_percent(0.375) == 37.5
    assert remaining_percent(1.0) == 100.0
    assert remaining_percent(None) is None


# ── model quota parsing ────────────────────────────────────────────────────

def test_parse_model_quota_reads_quota_info():
    row = parse_model_quota("gemini-3.8-flash-low", {
        "displayName": "Gemini 3.8 Flash",
        "quotaInfo": {"remainingFraction": 0.42, "resetTime": "2026-10-10T12:00:00Z"},
        "supportsThinking": True,
        "recommended": True,
    })
    assert row["modelId"] == "gemini-3.8-flash-low"
    assert row["displayName"] == "Gemini 3.8 Flash"
    assert row["remainingFraction"] == 0.42
    assert row["resetTime"] == "2026-10-10T12:00:00Z"
    assert row["supportsThinking"] is True
    assert row["recommended"] is True


def test_parse_model_quota_exhausted_flag():
    row = parse_model_quota("claude-opus-4-6", {
        "quotaInfo": {"remainingFraction": 0.0, "isExhausted": True},
    })
    assert row["isExhausted"] is True
    assert row["remainingFraction"] == 0.0


def test_parse_model_quota_skips_internal_and_chat_rows():
    assert parse_model_quota("chat_gemini", {"quotaInfo": {"remainingFraction": 1.0}}) is None
    assert parse_model_quota("tab_something", {"isInternal": True}) is None


def test_parse_model_quota_missing_quota_info_is_unknown_not_zero():
    row = parse_model_quota("gemini-2.5-pro", {"displayName": "Pro"})
    assert row["remainingFraction"] is None


def test_parse_models_quota_sorts_and_filters():
    data = {"models": {
        "gemini-3.8-flash-low": {"quotaInfo": {"remainingFraction": 0.9}},
        "chat_internal": {"quotaInfo": {"remainingFraction": 0.1}},
        "claude-sonnet-4-6": {"quotaInfo": {"remainingFraction": 0.5}},
    }}
    rows = parse_models_quota(data)
    ids = [r["modelId"] for r in rows]
    assert ids == ["claude-sonnet-4-6", "gemini-3.8-flash-low"]
    assert "chat_internal" not in ids


def test_parse_models_quota_empty_payload_is_empty_list():
    assert parse_models_quota({}) == []
    assert parse_models_quota({"models": {}}) == []
    assert parse_models_quota(None) == []


# ── aggregate quota groups (retrieveUserQuotaSummary) ──────────────────────

def test_parse_quota_groups_reads_buckets():
    data = {"groups": [
        {"displayName": "Gemini pool", "buckets": [
            {"bucketId": "b1", "displayName": "Daily", "remainingFraction": 0.3,
             "resetTime": "2026-10-10T00:00:00Z", "window": "1d"},
        ]},
    ]}
    groups = parse_quota_groups(data)
    assert len(groups) == 1
    bucket = groups[0]["buckets"][0]
    assert bucket["bucketId"] == "b1"
    assert bucket["remainingFraction"] == 0.3
    assert bucket["window"] == "1d"


def test_parse_quota_groups_skips_empty_rows():
    """A bucket with neither a fraction nor an id is not a real limit."""
    data = {"groups": [{"buckets": [{}]}]}
    assert parse_quota_groups(data) == []


def test_parse_quota_groups_keeps_group_without_buckets_if_named():
    data = {"groups": [{"displayName": "Named but empty"}]}
    groups = parse_quota_groups(data)
    assert len(groups) == 1
    assert groups[0]["buckets"] == []


# ── tier / plan label ──────────────────────────────────────────────────────

def test_parse_tier_reads_id_and_name():
    tier = parse_tier({"id": "g1-pro-tier", "name": "Google AI Pro"})
    assert tier["id"] == "g1-pro-tier"
    assert tier["name"] == "Google AI Pro"
    assert parse_tier({}) is None
    assert parse_tier(None) is None


def test_plan_label_prefers_the_paid_tier():
    """Google reports currentTier=free-tier even on paid Pro accounts."""
    label = plan_label(
        {"id": "free-tier", "name": "Free tier"},
        {"id": "g1-pro-tier", "name": "Google AI Pro"},
    )
    assert label == "Google AI Pro (g1-pro-tier)"


def test_plan_label_falls_back_to_current_tier():
    label = plan_label({"id": "free-tier", "name": "Free tier"}, None)
    assert label == "Free tier (free-tier)"


def test_plan_label_none_when_no_tier():
    assert plan_label(None, None) is None


# ── reset-time formatting ──────────────────────────────────────────────────

def test_format_reset_human_delta():
    now = 1_800_000_000.0
    from datetime import datetime, timezone
    future = datetime.fromtimestamp(now + (3 * 3600 + 12 * 60), tz=timezone.utc)
    assert format_reset(future.isoformat(), now) == "3h 12m"
    two_days = datetime.fromtimestamp(now + 2 * 86400 + 4 * 3600, tz=timezone.utc)
    assert format_reset(two_days.isoformat(), now) == "2d 4h"


def test_format_reset_past_is_now():
    now = 1_800_000_000.0
    from datetime import datetime, timezone
    past = datetime.fromtimestamp(now - 60, tz=timezone.utc)
    assert format_reset(past.isoformat(), now) == "now"


def test_format_reset_na_and_unparseable_are_visible():
    assert format_reset(None, 0) == "n/a"
    assert format_reset("not-a-date", 0) == "not-a-date"


# ── progress bar ───────────────────────────────────────────────────────────

def test_progress_bar_full_and_empty():
    assert progress_bar(1.0, width=10) == "[" + "#" * 10 + "]"
    assert progress_bar(0.0, width=10) == "[" + "-" * 10 + "]"


def test_progress_bar_unknown_is_question_marks():
    assert progress_bar(None, width=10) == "[" + "?" * 10 + "]"


# ── best model row (selection rule) ────────────────────────────────────────

def test_best_model_row_exact_match_is_authoritative():
    """An exact runtime-id hit wins even if a sibling shows more quota.

    The caller routed to that exact id, so its bucket is the one that will be
    charged — picking a different sibling would report the wrong bucket.
    """
    rows = [
        {"modelId": "gemini-3.8-flash-low", "remainingFraction": 0.1},
        {"modelId": "gemini-3.8-flash-medium", "remainingFraction": 0.8},
    ]
    picked = best_model_row(rows, "gemini-3.8-flash-low")
    assert picked["modelId"] == "gemini-3.8-flash-low"
    assert picked["remainingFraction"] == 0.1


def test_best_model_row_prefix_fallback_prefers_most_quota():
    """With no exact row, several runtime siblings share a bucket; pick the fullest."""
    rows = [
        {"modelId": "gemini-3.8-flash-low", "remainingFraction": 0.1},
        {"modelId": "gemini-3.8-flash-medium", "remainingFraction": 0.8},
    ]
    picked = best_model_row(rows, "gemini-3.8-flash")
    assert picked["modelId"] == "gemini-3.8-flash-medium"


def test_best_model_row_prefix_fallback():
    """A bare public id can still match its advertised runtime sibling."""
    rows = [{"modelId": "gemini-3.8-flash-low", "remainingFraction": 0.5}]
    picked = best_model_row(rows, "gemini-3.8-flash")
    assert picked["modelId"] == "gemini-3.8-flash-low"


def test_best_model_row_no_match_is_none():
    assert best_model_row([], "gemini-3.8-flash") is None


# ── edge cases (review follow-up) ───────────────────────────────────


def test_clamp_fraction_infinity_is_none_not_one():
    """A non-finite fraction must stay unknown, not clamp to 1.0.

    ``min(max(inf, 0.0), 1.0)`` would silently read as 'full quota', which is
    the one value we must never fabricate. NaN likewise yields None.
    """
    assert clamp_fraction(float("inf")) is None
    assert clamp_fraction(float("-inf")) is None
    assert clamp_fraction(float("nan")) is None


def test_format_reset_naive_timestamp_is_treated_as_utc():
    """A timestamp without a tz marker is read as UTC, not local time.

    ``format_reset`` fills a missing tz with UTC before computing the delta, so
    a naive ISO string measures the same regardless of the host's local zone.
    """
    # 2099-01-01T00:00:00Z minus a fixed 'now' exactly 3 days earlier.
    now = 4_070_649_600.0  # 2098-12-29T00:00:00Z
    out = format_reset("2099-01-01T00:00:00", now=now)
    assert out == "3d 0h"


def test_best_model_row_prefix_tie_prefers_deterministic_order():
    """Two prefix matches with equal quota: the first in list order wins.

    Not the most-quota rule (they tie), but a stable pick — never a random one.
    """
    rows = [
        {"modelId": "gemini-3.8-flash-low", "remainingFraction": 0.5,
         "resetTime": None, "isExhausted": False, "displayName": None,
         "supportsThinking": False, "supportsImages": False, "recommended": False},
        {"modelId": "gemini-3.8-flash-high", "remainingFraction": 0.5,
         "resetTime": None, "isExhausted": False, "displayName": None,
         "supportsThinking": False, "supportsImages": False, "recommended": False},
    ]
    row = best_model_row(rows, "gemini-3.8-flash")
    assert row is not None
    assert row["modelId"] == "gemini-3.8-flash-low"


def test_plan_label_paid_tier_with_id_but_no_name():
    """A paid tier carrying only an id (no name) does not label as that tier.

    ``plan_label`` prefers a tier with a human name; a bare-id paid tier falls
    back to the current tier's label. Assert that fallback rather than a
    fabricated label from the id alone.
    """
    current = {"id": "free", "name": "Free"}
    paid = {"id": "pro"}  # no 'name'
    label = plan_label(current, paid)
    assert label == "Free (free)"


def test_format_quota_report_license_error_becomes_paid_note():
    """A license/subscription error is explained as the paid-tier note.

    ``_group_error_note`` maps license/subscription_required/3501 errors to the
    pedagogical 'needs a paid subscription' note rather than echoing the raw
    relay error, which is the more useful operator fact. Assert the note, and
    that per-model quota is still reported as live.
    """
    report = format_quota_report(
        account_email="op@example.com",
        plan=None,
        groups=[],
        models=[],
        group_error="license: check your workspace license",
        now=0.0,
    )
    assert "paid subscription" in report.lower()
    assert "per-model quota below is still live" in report.lower()


# ── quota_is_low (the proactive-failover predicate) ─────────────────────────

def test_quota_is_low_zero_fraction():
    assert quota_is_low({"remainingFraction": 0.0}) is True


def test_quota_is_low_exhausted_flag_even_with_nonzero_fraction():
    assert quota_is_low({"remainingFraction": 0.3, "isExhausted": True}) is True


def test_quota_is_low_nonzero_is_not_low_by_default():
    """Default threshold is 0.0: a thin-but-usable bucket still gets its request."""
    assert quota_is_low({"remainingFraction": 0.05}) is False
    assert quota_is_low({"remainingFraction": 0.42}) is False


def test_quota_is_low_respects_a_raised_threshold():
    assert quota_is_low({"remainingFraction": 0.03}, threshold=0.05) is True
    assert quota_is_low({"remainingFraction": 0.1}, threshold=0.05) is False


def test_quota_is_low_unknown_is_never_low():
    """An unreadable quota must not be treated as an exhausted one."""
    assert quota_is_low(None) is False
    assert quota_is_low({}) is False
    assert quota_is_low({"remainingFraction": None}) is False


# ── report formatting ──────────────────────────────────────────────────────

def test_format_quota_report_renders_models_and_groups():
    report = format_quota_report(
        account_email="me@example.com",
        plan="Google AI Pro (g1-pro-tier)",
        groups=[{"displayName": "Gemini pool", "description": None, "buckets": [
            {"bucketId": "b1", "displayName": "Daily", "remainingFraction": 0.5,
             "window": "1d", "resetTime": None, "description": None}]}],
        models=[{"modelId": "gemini-3.8-flash-low", "displayName": "Flash",
                 "remainingFraction": 0.5, "resetTime": None, "isExhausted": False,
                 "supportsThinking": True, "supportsImages": False, "recommended": True}],
        group_error=None,
        now=1_800_000_000.0,
    )
    assert "me@example.com" in report
    assert "Google AI Pro" in report
    assert "Gemini pool" in report
    assert "50.0% left" in report
    assert "gemini-3.8-flash-low" in report
    assert "recommended" in report


def test_format_quota_report_surfaces_subscription_gate():
    """A 403 SUBSCRIPTION_REQUIRED must be explained, not shown as 'no quota'."""
    report = format_quota_report(
        account_email="me@example.com",
        plan=None,
        groups=[],
        models=[{"modelId": "m", "displayName": None, "remainingFraction": 0.1,
                 "resetTime": None, "isExhausted": False, "supportsThinking": False,
                 "supportsImages": False, "recommended": False}],
        group_error="endpoint HTTP 403: SUBSCRIPTION_REQUIRED #3501",
        now=1_800_000_000.0,
    )
    assert "paid subscription" in report
    assert "Per-model" in report
    assert "m" in report


def test_format_quota_report_unknown_fraction_shows_question_mark():
    report = format_quota_report(
        account_email=None, plan=None, groups=[],
        models=[{"modelId": "m", "displayName": None, "remainingFraction": None,
                 "resetTime": None, "isExhausted": False, "supportsThinking": False,
                 "supportsImages": False, "recommended": False}],
        group_error=None, now=0,
    )
    assert "?" in report


# ── the models.py seam (imperative shell) ──────────────────────────────────


class _Resp:
    """Minimal urlopen stand-in returning a canned JSON body."""

    def __init__(self, payload):
        self._payload = _json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._payload


def _seed_cache(monkeypatch_store=None):
    import models as models_mod
    models_mod._model_enum_cache.clear()
    models_mod._model_enum_loaded = False
    original = models_mod.MODEL_ENUM_STORE
    models_mod.MODEL_ENUM_STORE = Path(__import__("tempfile").mkdtemp()) / "enums.json"
    return models_mod, original


def _restore(models_mod, original):
    models_mod.MODEL_ENUM_STORE = original
    models_mod._model_enum_cache.clear()
    models_mod._model_enum_loaded = False


def test_fetch_account_quota_aggregates_three_rpcs():
    """The seam must call all three RPCs and merge their facts."""
    import models as models_mod

    models_payload = {"models": {"gemini-3.8-flash-low": {
        "model": "MODEL_PLACEHOLDER_M320",
        "quotaInfo": {"remainingFraction": 0.42, "resetTime": "2026-10-10T12:00:00Z"}}}}
    summary_payload = {"groups": [
        {"displayName": "Gemini pool", "buckets": [
            {"bucketId": "b1", "displayName": "Daily", "remainingFraction": 0.42}]}]}
    assist_payload = {"currentTier": {"id": "free-tier", "name": "Free tier"},
                      "paidTier": {"id": "g1-pro-tier", "name": "Google AI Pro"}}

    _, original = _seed_cache()
    try:
        def _fake_urlopen(req, timeout=None):
            path = req.full_url.split(".com")[-1]
            if "fetchAvailableModels" in path:
                return _Resp(models_payload)
            if "retrieveUserQuotaSummary" in path:
                return _Resp(summary_payload)
            if "loadCodeAssist" in path:
                return _Resp(assist_payload)
            raise AssertionError(f"unexpected path {path}")

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            snap = models_mod.fetch_account_quota("tok", "proj", email="me@example.com")

        assert snap["plan"] == "Google AI Pro (g1-pro-tier)"
        assert len(snap["groups"]) == 1
        assert snap["groups"][0]["buckets"][0]["remainingFraction"] == 0.42
        assert len(snap["models"]) == 1
        assert snap["models"][0]["remainingFraction"] == 0.42
        assert snap["groupError"] is None
        # quotaInfo must not be discarded: the model row carries the fraction
    finally:
        _restore(models_mod, original)


def test_fetch_account_quota_survives_subscription_gated_summary():
    """A 403 on the aggregate summary must not sink per-model quota."""
    import models as models_mod
    import urllib.error
    import io

    models_payload = {"models": {"gemini-3.8-flash-low": {
        "quotaInfo": {"remainingFraction": 0.9}}}}

    def _http_error(code, payload):
        return urllib.error.HTTPError("https://x", code, "err", {},
                                      io.BytesIO(_json.dumps(payload).encode("utf-8")))

    _, original = _seed_cache()
    try:
        def _fake_urlopen(req, timeout=None):
            path = req.full_url.split(".com")[-1]
            if "fetchAvailableModels" in path:
                return _Resp(models_payload)
            if "retrieveUserQuotaSummary" in path:
                raise _http_error(403, {"error": "SUBSCRIPTION_REQUIRED"})
            raise AssertionError(f"unexpected path {path}")

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            snap = models_mod.fetch_account_quota("tok", "proj", endpoints=["https://only.example"])

        # per-model quota is still measured
        assert snap["models"][0]["remainingFraction"] == 0.9
        # the aggregate gate is recorded, not hidden
        assert snap["groupError"] is not None
        assert "403" in snap["groupError"]
        assert snap["groups"] == []
    finally:
        _restore(models_mod, original)


def test_fetch_available_models_still_records_quota_enums():
    """The enum registration must keep working after the seam was added."""
    import models as models_mod

    payload = {"models": {
        "gemini-3.8-flash-low": {"model": "MODEL_PLACEHOLDER_M320",
                                 "quotaInfo": {"remainingFraction": 0.5}},
    }}
    _, original = _seed_cache()
    try:
        with patch("urllib.request.urlopen", return_value=_Resp(payload)):
            models_mod.fetch_available_models("tok", "proj")
        assert models_mod.get_model_enum("gemini-3.8-flash-low") == "MODEL_PLACEHOLDER_M320"
    finally:
        _restore(models_mod, original)



# ── proactive failover (client._switch_account_if_quota_low) ────────────────


def _two_account_registry(sandbox: Path):
    """A registry with two usable accounts; the last-added one is active.

    ``add_account`` makes the newly added account active, so ``fresh`` ends up
    active. The switch test therefore expects a move to the *other* account.
    """
    from accounts import AntigravityAccountRegistry

    reg = AntigravityAccountRegistry(registry_path=sandbox / "accounts.json")
    reg.add_account("first@example.com", {
        "access_token": "tok-first", "refresh_token": "rt",
        "expires_at": 9e9, "project_id": "proj-first"})
    reg.add_account("active@example.com", {
        "access_token": "tok-active", "refresh_token": "rt",
        "expires_at": 9e9, "project_id": "proj-active"})
    return reg


def _client_for(reg):
    """A client whose auth manager is bound to the given registry."""
    from unittest.mock import MagicMock
    from client import AntigravityClient
    from auth import AntigravityAuthManager

    mgr = AntigravityAuthManager(registry=reg)
    # get_credentials must hand back the ACTIVE account's token so a switch is
    # observable; the manager updates its credentials when the registry switches.
    mgr.get_credentials = MagicMock(
        side_effect=lambda: (
            mgr.credentials.get("access_token"),
            mgr.credentials.get("project_id", "antigravity-default"),
        )
    )
    return AntigravityClient(auth_manager=mgr, endpoints=["https://only.example"])


def _snapshot(models):
    return {"email": None, "projectId": None, "plan": None, "groups": [],
            "groupError": None, "models": models, "modelsError": None,
            "fetchedAt": 0.0}


def test_proactive_switch_fires_when_active_quota_empty():
    """An empty bucket on the active account must switch before sending."""
    import tempfile
    from pathlib import Path

    sandbox = Path(tempfile.mkdtemp())
    reg = _two_account_registry(sandbox)
    client = _client_for(reg)

    exhausted_snap = _snapshot([
        {"modelId": "gemini-3.8-flash-low", "remainingFraction": 0.0,
         "resetTime": None, "isExhausted": True, "displayName": None,
         "supportsThinking": False, "supportsImages": False, "recommended": False}])

    with patch("client.fetch_account_quota", return_value=exhausted_snap) as probe:
        token = client._switch_account_if_quota_low("gemini-3.8-flash-low", set())

    assert probe.called, "quota must be probed before deciding to switch"
    assert token == "tok-first", "must switch to the other account's token"
    assert reg.active_account_id != reg.get_account("active@example.com").account_id


def test_proactive_switch_skipped_when_quota_remains():
    """A bucket with quota left must NOT trigger a switch (threshold 0.0)."""
    import tempfile
    from pathlib import Path

    sandbox = Path(tempfile.mkdtemp())
    reg = _two_account_registry(sandbox)
    client = _client_for(reg)
    before = reg.active_account_id

    healthy_snap = _snapshot([
        {"modelId": "gemini-3.8-flash-low", "remainingFraction": 0.42,
         "resetTime": None, "isExhausted": False, "displayName": None,
         "supportsThinking": False, "supportsImages": False, "recommended": False}])

    with patch("client.fetch_account_quota", return_value=healthy_snap):
        token = client._switch_account_if_quota_low("gemini-3.8-flash-low", set())

    assert token is None
    assert reg.active_account_id == before, "a usable bucket must not switch accounts"


def test_proactive_switch_skipped_with_single_account():
    """One account has nowhere to switch to; the probe must not even run."""
    import tempfile
    from pathlib import Path
    from accounts import AntigravityAccountRegistry

    sandbox = Path(tempfile.mkdtemp())
    reg = AntigravityAccountRegistry(registry_path=sandbox / "accounts.json")
    reg.add_account("only@example.com", {
        "access_token": "tok", "refresh_token": "rt",
        "expires_at": 9e9, "project_id": "proj"})
    client = _client_for(reg)

    with patch("client.fetch_account_quota") as probe:
        token = client._switch_account_if_quota_low("gemini-3.8-flash-low", set())

    assert not probe.called, "a single account must not pay a quota-probe round-trip"
    assert token is None


def test_proactive_switch_survives_a_quota_probe_failure():
    """A flaky/unreachable quota read must never block the request."""
    import tempfile
    from pathlib import Path

    sandbox = Path(tempfile.mkdtemp())
    reg = _two_account_registry(sandbox)
    client = _client_for(reg)
    before = reg.active_account_id

    with patch("client.fetch_account_quota", side_effect=RuntimeError("network down")):
        token = client._switch_account_if_quota_low("gemini-3.8-flash-low", set())

    assert token is None
    assert reg.active_account_id == before, "a failed probe must not move the account"


def test_generate_uses_switched_token_when_quota_empty():
    """End-to-end: generate() must send with the fresh account's token."""
    import tempfile
    from pathlib import Path
    from unittest.mock import MagicMock

    sandbox = Path(tempfile.mkdtemp())
    reg = _two_account_registry(sandbox)
    client = _client_for(reg)

    exhausted_snap = _snapshot([
        {"modelId": "gemini-3.8-flash-low", "remainingFraction": 0.0,
         "resetTime": None, "isExhausted": True, "displayName": None,
         "supportsThinking": False, "supportsImages": False, "recommended": False}])

    sse = [b'data: {"response":{"candidates":[{"content":{"parts":[{"text":"ok"}]},"finishReason":"STOP"}]}}\n\n',
           b"data: [DONE]\n\n"]
    mock_resp = MagicMock()
    mock_resp.__iter__.return_value = sse
    mock_resp.status = 200

    captured = {}

    def _capture(req, timeout=None):
        captured["auth"] = req.headers.get("Authorization")
        return mock_resp

    with patch("client.fetch_account_quota", return_value=exhausted_snap), \
         patch("urllib.request.urlopen", side_effect=_capture):
        client.chat.completions.create(
            model="gemini-3.8-flash",
            messages=[{"role": "user", "content": "hi"}],
            stream=False,
        )

    assert captured["auth"] == "Bearer tok-first", (
        "request must carry the switched account's bearer, not the exhausted one")


def test_switched_account_project_id_is_new_not_stale():
    """Bug regression: after a proactive switch the request must carry the NEW
    account's project_id, not the old one.

    ``generate`` used to re-read project_id from ``self.auth.get_credentials()``,
    but the manager stays bound to the previous (exhausted) account, so the
    request went out with the new account's token and the OLD account's project
    — a mismatch that 404s or misattributes quota. Assert the payload carries
    the switched account's project (``proj-first``), not ``proj-active``.
    """
    import json as _json
    import tempfile
    from pathlib import Path
    from unittest.mock import MagicMock

    sandbox = Path(tempfile.mkdtemp())
    reg = _two_account_registry(sandbox)
    client = _client_for(reg)

    exhausted_snap = _snapshot([
        {"modelId": "gemini-3.8-flash-low", "remainingFraction": 0.0,
         "resetTime": None, "isExhausted": True, "displayName": None,
         "supportsThinking": False, "supportsImages": False, "recommended": False}])

    sse = [b'data: {"response":{"candidates":[{"content":{"parts":[{"text":"ok"}]},"finishReason":"STOP"}]}}\n\n',
           b"data: [DONE]\n\n"]
    mock_resp = MagicMock()
    mock_resp.__iter__.return_value = sse
    mock_resp.status = 200

    captured = {}

    def _capture(req, timeout=None):
        captured["auth"] = req.headers.get("Authorization")
        captured["body"] = req.data
        return mock_resp

    with patch("client.fetch_account_quota", return_value=exhausted_snap), \
         patch("urllib.request.urlopen", side_effect=_capture):
        client.chat.completions.create(
            model="gemini-3.8-flash",
            messages=[{"role": "user", "content": "hi"}],
            stream=False,
        )

    assert captured["auth"] == "Bearer tok-first"
    payload = _json.loads(captured["body"].decode("utf-8"))
    assert payload.get("project") == "proj-first", (
        "request must carry the switched account's project, not the stale one")


def test_failover_refreshes_the_new_accounts_refresh_token():
    """Bug regression: reactive 429 failover must exchange the NEW account's
    refresh token, not the exhausted account's.

    The target account has no access_token (only a refresh token). The old code
    called ``self.auth.refresh_access_token()``, but the manager is still bound
    to the exhausted account, so it exchanged the OLD refresh token and handed
    back the OLD token — the failover retried the wall it was meant to escape.
    Assert the exchange used the second account's refresh token.
    """
    import tempfile
    from pathlib import Path
    from unittest.mock import MagicMock

    sandbox = Path(tempfile.mkdtemp())
    from accounts import AntigravityAccountRegistry
    reg = AntigravityAccountRegistry(registry_path=sandbox / "accounts.json")
    reg.add_account("exhausted@example.com", {
        "access_token": "tok-exhausted", "refresh_token": "rt-old",
        "expires_at": 9e9, "project_id": "proj-old"})
    # Target account: refresh-token only, so failover must mint a token for IT.
    reg.add_account("target@example.com", {
        "access_token": "", "refresh_token": "rt-new",
        "expires_at": 9e9, "project_id": "proj-new"})

    client = _client_for(reg)

    # In the real request flow, the active (exhausted) account is already in
    # tried_account_ids before failover runs, so the target is the only untried
    # one. Mirror that here: add the exhausted account's id to the tried set.
    exhausted_id = reg.get_account("exhausted@example.com").account_id
    target_id = reg.get_account("target@example.com").account_id

    seen_refresh = {}

    def _fake_refresh(self):
        # The failover builds a fresh manager pinned to the target account, so
        # ``self`` here is that new-account manager. Assert its refresh token
        # is the target's, proving the exchange targeted the right account.
        seen_refresh["token"] = self.credentials.get("refresh_token")
        seen_refresh["account_id"] = self.account_id
        return "tok-target"

    with patch("auth.AntigravityAuthManager.refresh_access_token", _fake_refresh):
        token = client._failover_to_next_account({exhausted_id})

    assert token == "tok-target"
    assert seen_refresh.get("token") == "rt-new", (
        "failover must exchange the TARGET account's refresh token, not the exhausted one")
    assert seen_refresh.get("account_id") == target_id, (
        "the refresh must be pinned to the target account, not the exhausted one")


def test_get_registry_reads_the_real_attribute():
    """Regression: the auth manager stores its registry as ``_registry``.

    Both failover paths used to read ``self.auth.registry`` — a public name the
    manager never sets — so the lookup was always None and the 429 account
    failover silently never fired. The switch was only observable once this
    resolved the real ``_registry`` attribute.
    """
    import tempfile
    from pathlib import Path

    sandbox = Path(tempfile.mkdtemp())
    reg = _two_account_registry(sandbox)
    client = _client_for(reg)

    assert client._get_registry() is reg
    assert not hasattr(client.auth, "registry"), (
        "the manager must not gain a public 'registry'; reading it was the bug")


def test_reactive_429_failover_now_fires():
    """The pre-existing 429 failover was dead (dead 'registry' accessor).

    Guard the fix: a 429 on the active account must now actually switch to the
    next linked account instead of silently burning the backoff.
    """
    import io
    import tempfile
    import urllib.error
    from pathlib import Path
    from unittest.mock import MagicMock

    sandbox = Path(tempfile.mkdtemp())
    reg = _two_account_registry(sandbox)
    client = _client_for(reg)
    # two endpoints: the 429 path failovers account, then retries on the next
    # candidate. A single endpoint would end the loop with nothing to retry.
    client.endpoints = ["https://one.example", "https://two.example"]

    def _http_error(code):
        return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(b"{}"))

    ok_resp = MagicMock()
    ok_resp.__iter__.return_value = [
        b'data: {"response":{"candidates":[{"content":{"parts":[{"text":"ok"}]},"finishReason":"STOP"}]}}\n\n',
        b"data: [DONE]\n\n"]
    ok_resp.status = 200

    calls = {"n": 0, "auth": []}

    def _side(req, timeout=None):
        calls["n"] += 1
        calls["auth"].append(req.headers.get("Authorization"))
        if calls["n"] == 1:
            raise _http_error(429)   # first attempt hits the quota wall
        return ok_resp               # second attempt (switched account) succeeds

    # no proactive probe here: exercise the *reactive* path directly
    with patch("client.fetch_account_quota", side_effect=AssertionError("no probe expected")), \
         patch("urllib.request.urlopen", side_effect=_side):
        client.chat.completions.create(
            model="gemini-3.8-flash",
            messages=[{"role": "user", "content": "hi"}],
            stream=False,
        )

    assert calls["n"] == 2, "must retry once after the 429"
    assert calls["auth"][0] != calls["auth"][1], (
        "the retry must carry the OTHER account's bearer, proving failover fired")


# ── hermes auth status antigravity (quota in the status surface) ────────────


def _status_with_pool(entries, account, capsys=None):
    """Drive _antigravity_auth_status with a stubbed credential pool.

    Returns the printed lines. The pool module is swapped the way test_auth
    does, so no real Hermes pool is touched.
    """
    import io as _io
    import sys
    from contextlib import redirect_stdout

    import auth as auth_mod

    class _Pool:
        def entries(self):
            return entries

    fake = type(sys)("agent.credential_pool")
    fake.load_pool = lambda name: _Pool()
    saved = sys.modules.get("agent.credential_pool")
    sys.modules["agent.credential_pool"] = fake
    buf = _io.StringIO()
    try:
        with redirect_stdout(buf), \
             patch.object(auth_mod, "AntigravityAccountRegistry") as reg_cls, \
             patch.object(auth_mod, "_print_quota_status") as quota_ln:
            reg_cls.return_value.get_account.return_value = account
            handled = auth_mod.antigravity_auth_handler(
                "status", type("A", (), {"provider": "antigravity"})())
    finally:
        if saved is not None:
            sys.modules["agent.credential_pool"] = saved
        else:
            sys.modules.pop("agent.credential_pool", None)
    return handled, buf.getvalue(), quota_ln


def _entry(expired=False):
    import time as _t
    from datetime import datetime, timezone
    stamp = datetime.fromtimestamp(
        _t.time() + (-3600 if expired else 3600), tz=timezone.utc).isoformat()
    return type("E", (), {
        "access_token": "ya29.x", "agent_key": "",
        "expires_at": stamp, "base_url": "https://daily-cloudcode-pa.googleapis.com",
    })()


def test_status_owned_and_reproduces_pool_facts():
    """Claiming status must still print the core's pool facts, not just quota."""
    handled, out, _ = _status_with_pool([_entry()], account=_acct())
    assert handled is True
    assert "antigravity: logged in" in out
    assert "accounts: 1" in out
    assert "api_base_url" in out
    assert "account: operator@example.com" in out


def test_status_logged_out_when_pool_empty():
    handled, out, quota_ln = _status_with_pool([], account=None)
    assert handled is True
    assert "logged out" in out
    assert not quota_ln.called, "no quota probe when there is no live account"


def test_status_logged_out_when_only_expired_entries():
    handled, out, quota_ln = _status_with_pool([_entry(expired=True)], account=_acct())
    assert handled is True
    assert "logged out" in out
    assert not quota_ln.called


def _acct():
    return type("A", (), {
        "account_id": "acc1", "email": "operator@example.com",
        "credentials": {"access_token": "tok", "project_id": "proj"},
    })()


def test_status_quota_probe_failure_never_breaks_status(capsys=None):
    """A dead relay must degrade the quota line, not the whole status."""
    import tempfile
    from pathlib import Path

    from accounts import AntigravityAccountRegistry

    sandbox = Path(tempfile.mkdtemp())
    reg = AntigravityAccountRegistry(registry_path=sandbox / "accounts.json")
    reg.add_account("operator@example.com", {
        "access_token": "tok", "refresh_token": "rt", "expires_at": 9e9,
        "project_id": "proj"})
    account = reg.get_account()

    import auth as auth_mod
    with patch.object(auth_mod, "AntigravityAuthManager") as mgr_cls, \
         patch("models.fetch_account_quota", side_effect=RuntimeError("relay down")):
        mgr_cls.return_value.get_credentials.return_value = ("tok", "proj")
        out = _capture(auth_mod._print_quota_status, reg, account)

    assert "unavailable" in out
    assert "relay down" in out


def test_status_quota_summary_when_paid():
    import tempfile
    from pathlib import Path

    from accounts import AntigravityAccountRegistry

    sandbox = Path(tempfile.mkdtemp())
    reg = AntigravityAccountRegistry(registry_path=sandbox / "accounts.json")
    reg.add_account("operator@example.com", {
        "access_token": "tok", "refresh_token": "rt", "expires_at": 9e9,
        "project_id": "proj"})
    account = reg.get_account()

    snapshot = {
        "plan": "Google AI Pro (g1-pro-tier)",
        "groups": [{"displayName": "Gemini pool", "buckets": [
            {"displayName": "Daily prompt", "remainingFraction": 0.42}]}],
        "models": [], "groupError": None,
    }
    import auth as auth_mod
    with patch.object(auth_mod, "AntigravityAuthManager") as mgr_cls, \
         patch("models.fetch_account_quota", return_value=snapshot):
        mgr_cls.return_value.get_credentials.return_value = ("tok", "proj")
        out = _capture(auth_mod._print_quota_status, reg, account)

    assert "plan: Google AI Pro" in out
    assert "Gemini pool" in out
    assert "42.0%" in out


def test_status_quota_freetier_falls_back_to_per_model():
    import tempfile
    from pathlib import Path

    from accounts import AntigravityAccountRegistry

    sandbox = Path(tempfile.mkdtemp())
    reg = AntigravityAccountRegistry(registry_path=sandbox / "accounts.json")
    reg.add_account("operator@example.com", {
        "access_token": "tok", "refresh_token": "rt", "expires_at": 9e9,
        "project_id": "proj"})
    account = reg.get_account()

    snapshot = {
        "plan": "Free tier (free-tier)",
        "groups": [],
        "groupError": "endpoint HTTP 403: SUBSCRIPTION_REQUIRED",
        "models": [
            {"modelId": "m1", "remainingFraction": 0.0, "isExhausted": True},
            {"modelId": "m2", "remainingFraction": 0.5, "isExhausted": False},
        ],
    }
    import auth as auth_mod
    with patch.object(auth_mod, "AntigravityAuthManager") as mgr_cls, \
         patch("models.fetch_account_quota", return_value=snapshot):
        mgr_cls.return_value.get_credentials.return_value = ("tok", "proj")
        out = _capture(auth_mod._print_quota_status, reg, account)

    assert "per-model only" in out
    assert "2 models advertised, 1 exhausted" in out


def _capture(fn, *args):
    import io as _io
    from contextlib import redirect_stdout

    buf = _io.StringIO()
    with redirect_stdout(buf):
        fn(*args)
    return buf.getvalue()


# ── _cli_quota (auth.py quota command) ──────────────────────────────


def _registry_empty(sandbox: Path):
    from accounts import AntigravityAccountRegistry
    return AntigravityAccountRegistry(registry_path=sandbox / "accounts.json")


def test_cli_quota_reports_no_accounts():
    """Empty registry: reported as 'no accounts', not as 0% quota."""
    import sys as _sys
    import tempfile
    from pathlib import Path

    import auth as auth_mod

    sandbox = Path(tempfile.mkdtemp())
    reg = _registry_empty(sandbox)

    argv = _sys.argv
    _sys.argv = ["auth.py", "quota"]
    try:
        out = _capture(auth_mod._cli_quota, reg)
    finally:
        _sys.argv = argv

    assert "no accounts" in out.lower()


def test_cli_quota_json_error_when_no_accounts():
    """--json emits a machine-readable error object, not a crash."""
    import json as _json
    import sys as _sys
    import tempfile
    from pathlib import Path

    import auth as auth_mod

    sandbox = Path(tempfile.mkdtemp())
    reg = _registry_empty(sandbox)

    argv = _sys.argv
    _sys.argv = ["auth.py", "quota", "--json"]
    try:
        out = _capture(auth_mod._cli_quota, reg)
    finally:
        _sys.argv = argv

    payload = _json.loads(out)
    assert payload["ok"] is False
    assert "no accounts" in payload["error"].lower()


def test_cli_quota_reports_credentials_failure():
    """Credential resolution failure is surfaced, not shown as 0% left."""
    import sys as _sys
    import tempfile
    from pathlib import Path

    import auth as auth_mod
    from accounts import AntigravityAccountRegistry

    sandbox = Path(tempfile.mkdtemp())
    reg = AntigravityAccountRegistry(registry_path=sandbox / "accounts.json")
    reg.add_account("operator@example.com", {
        "access_token": "tok", "refresh_token": "rt",
        "expires_at": 9e9, "project_id": "proj"})

    argv = _sys.argv
    _sys.argv = ["auth.py", "quota"]
    try:
        with patch.object(auth_mod, "AntigravityAuthManager") as mgr_cls:
            mgr_cls.return_value.get_credentials.side_effect = RuntimeError("no refresh token")
            out = _capture(auth_mod._cli_quota, reg)
    finally:
        _sys.argv = argv

    assert "credentials" in out.lower()
    assert "no refresh token" in out


def test_cli_quota_reports_fetch_failure():
    """An unreachable relay is reported as unreachable, not as exhausted quota."""
    import sys as _sys
    import tempfile
    from pathlib import Path

    import auth as auth_mod
    from accounts import AntigravityAccountRegistry

    sandbox = Path(tempfile.mkdtemp())
    reg = AntigravityAccountRegistry(registry_path=sandbox / "accounts.json")
    reg.add_account("operator@example.com", {
        "access_token": "tok", "refresh_token": "rt",
        "expires_at": 9e9, "project_id": "proj"})

    argv = _sys.argv
    _sys.argv = ["auth.py", "quota"]
    try:
        with patch.object(auth_mod, "AntigravityAuthManager") as mgr_cls, \
             patch("models.fetch_account_quota", side_effect=RuntimeError("relay down")):
            mgr_cls.return_value.get_credentials.return_value = ("tok", "proj")
            out = _capture(auth_mod._cli_quota, reg)
    finally:
        _sys.argv = argv

    assert "unreachable" in out.lower()
    assert "relay down" in out


def test_cli_quota_json_prints_snapshot():
    """--json on success prints the raw snapshot as machine-readable JSON."""
    import json as _json
    import sys as _sys
    import tempfile
    from pathlib import Path

    import auth as auth_mod
    from accounts import AntigravityAccountRegistry

    sandbox = Path(tempfile.mkdtemp())
    reg = AntigravityAccountRegistry(registry_path=sandbox / "accounts.json")
    reg.add_account("operator@example.com", {
        "access_token": "tok", "refresh_token": "rt",
        "expires_at": 9e9, "project_id": "proj"})

    snapshot = {
        "email": "operator@example.com", "projectId": "proj", "plan": None,
        "groups": [], "groupError": None, "modelsError": None,
        "models": [{"modelId": "gemini-3.8-flash-low", "remainingFraction": 0.5,
                    "resetTime": None, "isExhausted": False, "displayName": None,
                    "supportsThinking": False, "supportsImages": False,
                    "recommended": True}],
        "fetchedAt": 0.0,
    }

    argv = _sys.argv
    _sys.argv = ["auth.py", "quota", "--json"]
    try:
        with patch.object(auth_mod, "AntigravityAuthManager") as mgr_cls, \
             patch("models.fetch_account_quota", return_value=snapshot):
            mgr_cls.return_value.get_credentials.return_value = ("tok", "proj")
            out = _capture(auth_mod._cli_quota, reg)
    finally:
        _sys.argv = argv

    payload = _json.loads(out)
    assert payload["models"][0]["modelId"] == "gemini-3.8-flash-low"
    assert payload["models"][0]["remainingFraction"] == 0.5


def test_cli_quota_text_prints_report():
    """Default (text) output renders the human report, not raw JSON."""
    import sys as _sys
    import tempfile
    from pathlib import Path

    import auth as auth_mod
    from accounts import AntigravityAccountRegistry

    sandbox = Path(tempfile.mkdtemp())
    reg = AntigravityAccountRegistry(registry_path=sandbox / "accounts.json")
    reg.add_account("operator@example.com", {
        "access_token": "tok", "refresh_token": "rt",
        "expires_at": 9e9, "project_id": "proj"})

    snapshot = {
        "email": "operator@example.com", "projectId": "proj", "plan": None,
        "groups": [], "groupError": None, "modelsError": None,
        "models": [{"modelId": "gemini-3.8-flash-low", "remainingFraction": 0.5,
                    "resetTime": None, "isExhausted": False, "displayName": None,
                    "supportsThinking": False, "supportsImages": False,
                    "recommended": True}],
        "fetchedAt": 0.0,
    }

    argv = _sys.argv
    _sys.argv = ["auth.py", "quota"]
    try:
        with patch.object(auth_mod, "AntigravityAuthManager") as mgr_cls, \
             patch("models.fetch_account_quota", return_value=snapshot):
            mgr_cls.return_value.get_credentials.return_value = ("tok", "proj")
            out = _capture(auth_mod._cli_quota, reg)
    finally:
        _sys.argv = argv

    assert "Antigravity quota" in out
    assert "gemini-3.8-flash-low" in out
