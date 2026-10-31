"""Integration-style test for the multi-account flow.

Two accounts live in one registry. The active account is exhausted (HTTP 429)
and the second account only carries a refresh token. The reactive 429 path
must switch to that second account and mint a fresh access token from ITS
refresh token, then retry the request with the new bearer. No real HTTP is
performed: both the network opener and the OAuth refresh are mocked.
"""
import io
import json
import tempfile
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from accounts import AntigravityAccountRegistry
from client import AntigravityClient
from auth import AntigravityAuthManager


def _client_for(reg):
    from unittest.mock import MagicMock

    mgr = AntigravityAuthManager(registry=reg)
    mgr.get_credentials = MagicMock(
        side_effect=lambda: (
            mgr.credentials.get("access_token"),
            mgr.credentials.get("project_id", "antigravity-default"),
        )
    )
    return AntigravityClient(auth_manager=mgr, endpoints=["https://one.example", "https://two.example"])


def test_multi_account_429_failover_uses_second_accounts_refresh_token():
    sandbox = Path(tempfile.mkdtemp())
    reg = AntigravityAccountRegistry(registry_path=sandbox / "accounts.json")

    # Target account first: refresh-token only, so failover must mint for it.
    reg.add_account("target@example.com", {
        "access_token": "",
        "refresh_token": "rt-new",
        "expires_at": 9e9,
        "project_id": "proj-new",
    })
    # Active (exhausted) account added last, so it becomes active.
    reg.add_account("exhausted@example.com", {
        "access_token": "tok-exhausted",
        "refresh_token": "rt-old",
        "expires_at": 9e9,
        "project_id": "proj-old",
    })

    exhausted_id = reg.get_account("exhausted@example.com").account_id
    target_id = reg.get_account("target@example.com").account_id

    client = _client_for(reg)

    seen = {}

    def _fake_refresh(self):
        seen["refresh_token"] = self.credentials.get("refresh_token")
        seen["account_id"] = self.account_id
        # Persist the minted token the way the real refresh does, so the
        # registry reflects the failover outcome.
        self.credentials["access_token"] = "tok-target"
        self.credentials["expires_at"] = 9e9
        self._persist_credentials()
        return "tok-target"

    ok_resp = MagicMock()
    ok_resp.__iter__.return_value = [
        b'data: {"response":{"candidates":[{"content":{"parts":[{"text":"ok"}],"finishReason":"STOP"}]}}\n\n',
        b"data: [DONE]\n\n",
    ]
    ok_resp.status = 200

    calls = {"n": 0, "auth": []}

    def _side(req, timeout=None):
        calls["n"] += 1
        calls["auth"].append(req.headers.get("Authorization"))
        if calls["n"] == 1:
            raise urllib.error.HTTPError(
                "https://x", 429, "err", {}, io.BytesIO(b"{}")
            )
        return ok_resp

    with patch("client.fetch_account_quota", side_effect=AssertionError("no proactive probe")), \
         patch("auth.AntigravityAuthManager.refresh_access_token", _fake_refresh), \
         patch("models.DEFAULT_OPENER.open", side_effect=_side):
        resp = client.chat.completions.create(
            model="gemini-3.8-flash",
            messages=[{"role": "user", "content": "hi"}],
            stream=False,
        )

    assert calls["n"] == 2, "must retry once after the 429"
    assert calls["auth"][0] == "Bearer tok-exhausted"
    assert calls["auth"][1] == "Bearer tok-target", (
        "retry must carry the second account's freshly minted bearer")
    assert seen.get("refresh_token") == "rt-new", (
        "failover must exchange the second account's refresh token")
    assert seen.get("account_id") == target_id
    assert reg.active_account_id == target_id, "failover must activate the second account"
    assert reg.get_account(target_id).credentials.get("access_token") == "tok-target", (
        "the minted token must be persisted to the second account")
    assert resp is not None