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

import quota
from quota import (
    clamp_fraction,
    remaining_percent,
    parse_quota_buckets,
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

    models_mod, original = _seed_cache()
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

    models_mod, original = _seed_cache()
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
    models_mod, original = _seed_cache()
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
    from unittest.mock import MagicMock

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
    import json as _j
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
