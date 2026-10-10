import os
import tempfile
from unittest.mock import patch
from auth import (
    generate_pkce,
    load_local_token,
    save_token_cache,
    extract_project_id,
    AntigravityAuthManager,
)


def test_generate_pkce():
    verifier, challenge = generate_pkce()
    assert len(verifier) >= 43
    assert len(challenge) >= 43
    assert verifier != challenge


def test_extract_project_id():
    assert extract_project_id({"projectId": "proj-123"}) == "proj-123"
    assert extract_project_id({"cloudaicompanionProject": "proj-456"}) == "proj-456"
    assert extract_project_id({"antigravityProjectId": "proj-789"}) == "proj-789"
    assert extract_project_id({"nested": {"id": "p1"}}) is None


def test_save_and_load_token_cache():
    with patch.dict(os.environ, {}, clear=True):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = os.path.join(tmpdir, "antigravity-auth.json")
            data = {
                "access_token": "mock-access-token-123",
                "refresh_token": "mock-refresh-token-456",
                "expires_at": 9999999999.0,
                "project_id": "test-project-001",
            }
            save_token_cache(data, cache_path=cache_path)

            loaded = load_local_token(cache_paths=[cache_path])
            assert loaded["access_token"] == "mock-access-token-123"
            assert loaded["project_id"] == "test-project-001"


def test_auth_manager_get_valid_token_cached():
    manager = AntigravityAuthManager(
        credentials={
            "access_token": "mock-valid-token-789",
            "refresh_token": "mock-refresh-token-456",
            "expires_at": 9999999999.0,
            "project_id": "proj-abc",
        }
    )
    token, proj = manager.get_credentials()
    assert token == "mock-valid-token-789"


def test_register_removal_step_returns_bool():
    from auth import _register_removal_step
    # Must never raise: True when the core registry accepts the step,
    # False when the core removed the shim (add/list/status unaffected).
    assert _register_removal_step() in (True, False)


def test_callback_uri_is_derived_from_the_listener_port():
    """The registered redirect must match the port the listener actually binds.

    Google has the redirect URI registered as a literal, so the two cannot drift:
    the port is read from _CALLBACK_PORT rather than hardcoded a second time.
    """
    from auth import _CALLBACK_HOST, _CALLBACK_PORT, REDIRECT_URI

    assert REDIRECT_URI == f"http://localhost:{_CALLBACK_PORT}/oauth-callback"
    assert _CALLBACK_PORT == 51121, (
        "the registered redirect URI pins this port; changing it needs a new OAuth client")
    assert _CALLBACK_HOST == "127.0.0.1", "the callback must bind loopback only"


def test_busy_callback_port_is_reported_with_its_reason():
    """A busy port must be named instead of swallowed.

    Two Hermes profiles can each trigger a login, and the second one used to lose
    its callback silently: `except Exception: pass` swallowed EADDRINUSE and the
    user was told the callback "did not work", sending them to the wrong fix. The
    manual paste fallback still works, so it must be reached with the real reason
    shown.
    """
    import io as _io
    import socket
    from contextlib import redirect_stdout

    import auth as auth_mod

    # Occupy the real port so HTTPServer raises the same EADDRINUSE a second
    # concurrent login would.
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        blocker.bind((auth_mod._CALLBACK_HOST, auth_mod._CALLBACK_PORT))
        blocker.listen(1)
    except OSError:
        blocker.close()
        return  # port unavailable in this environment; nothing to assert

    buffer = _io.StringIO()
    from accounts import AntigravityAccountRegistry
    manager = auth_mod.AntigravityAuthManager(
        registry=AntigravityAccountRegistry(
            registry_path=__import__('pathlib').Path(__import__('tempfile').mkdtemp()) / 'accounts.json'))
    try:
        with redirect_stdout(buffer), \
                patch("webbrowser.open"), \
                patch("builtins.input", side_effect=EOFError):
            try:
                manager.login_interactive()
            except Exception:
                pass  # a cancelled manual paste ends the login
    finally:
        blocker.close()

    output = buffer.getvalue()
    assert str(auth_mod._CALLBACK_PORT) in output, (
        f"the taken port must be named in the message; got: {output!r}")
    assert "already in use" in output, (
        f"the message must say the port is in use; got: {output!r}")


def test_callback_timeout_is_short_without_a_terminal():
    """Catalog rule 12: unattended runs must fail fast, not wait out the callback window."""
    import io
    import sys
    import auth as auth_mod

    assert auth_mod._callback_timeout() == (120.0 if sys.stdin.isatty() else 5.0)
    with patch("sys.stdin", io.StringIO()):
        assert auth_mod._callback_timeout() == 5.0


def test_provider_declares_public_auth_hooks():
    """The profile must carry auth_handler/refresh_credential, not private writes.

    Catalog admission rule 9 refuses a plugin that rebinds Hermes core at
    runtime, and `hermes plugins validate` enforces it statically. Declaring the
    documented hooks on ProviderProfile is the supported seam.
    """
    import __init__ as plugin
    from providers import get_provider_profile

    profile = get_provider_profile("antigravity")
    assert profile is not None
    assert profile.auth_handler is not None, "auth_handler must be declared"
    assert profile.refresh_credential is not None, "refresh_credential must be declared"
    assert getattr(profile, "_oauth_auth_type", None) is None, (
        "_oauth_auth_type is not read by any core release; drop it")
    assert plugin.register_provider is not None


def test_auth_handler_declines_actions_the_core_owns():
    """Only `add` and `status` are ours; logout/refresh/unknown fall through.

    `status` is claimed so it can append live quota (the core prints nothing
    more once we return True), while `logout` stays core-owned and `refresh` is
    driven by the pool's own rotation via refresh_credential.
    """
    from auth import antigravity_auth_handler

    for action in ("logout", "refresh", "unknown"):
        assert antigravity_auth_handler(action, None) is False, (
            f"{action} must fall through to the core path")


def test_auth_handler_add_writes_one_pooled_row(monkeypatch=None):
    """`hermes auth add antigravity` must fill the pool the status path reads."""
    import sys
    from unittest.mock import patch

    import auth as auth_mod

    calls = {}

    class _Pool:
        def __init__(self):
            self.added = []

        def entries(self):
            return []

        def add(self, label, source, token, fields):
            calls["add"] = {"label": label, "source": source, "token": token, "fields": fields}

        def save(self):
            calls["saved"] = True

    class _PoolModule:
        @staticmethod
        def load_pool(name):
            calls["provider"] = name
            return _Pool()

    # stub the login: the handler must be exercised without a real browser flow
    fake_login = {
        "access_token": "ya29.test-token",
        "refresh_token": "1//refresh",
        "expires_at": 9e9,
        "project_id": "proj-test",
        "email": "titouan@example.com",
    }
    fake = type(sys)("agent.credential_pool")
    fake.load_pool = _PoolModule.load_pool
    saved = sys.modules.get("agent.credential_pool")
    sys.modules["agent.credential_pool"] = fake
    try:
        with patch.object(auth_mod, "_antigravity_oauth_login", return_value=fake_login):
            handled = auth_mod.antigravity_auth_handler("add", type("A", (), {"label": None})())
    finally:
        if saved is not None:
            sys.modules["agent.credential_pool"] = saved
        else:
            sys.modules.pop("agent.credential_pool", None)

    assert handled is True
    assert calls.get("provider") == "antigravity"
    assert calls["add"]["source"] == "manual:antigravity_pkce"
    assert calls["add"]["token"]
    assert calls.get("saved") is True
    assert "titouan@example.com" in calls["add"]["label"], (
        "the pool row must be labelled with the account that signed in")


def test_auth_add_warns_about_google_tos_before_login(monkeypatch=None):
    """``hermes auth add antigravity`` must warn about the ToS before OAuth.

    Google's Antigravity Terms prohibit third-party OAuth access and carry a
    suspension/termination risk; enforcement is active. The warning is a
    non-negotiable part of the add path, shown before the login flow starts.
    """
    import io
    import sys
    import tempfile
    from contextlib import redirect_stdout
    from pathlib import Path
    from unittest.mock import patch

    import auth as auth_mod

    fake_login = {
        "email": "titouan@example.com",
        "access_token": "tok", "refresh_token": "rt",
        "expires_at": 9e9, "project_id": "proj", "scope": "",
    }

    sandbox = Path(tempfile.mkdtemp())
    monkeypatch = __import__("os").environ
    monkeypatch["HERMES_HOME"] = str(sandbox)

    class _Pool:
        def entries(self):
            return []
        def add(self, *a, **k):
            return None
        def save(self):
            return None

    fake = type(sys)("agent.credential_pool")
    fake.load_pool = lambda *a, **k: _Pool()
    saved = sys.modules.get("agent.credential_pool")
    sys.modules["agent.credential_pool"] = fake
    try:
        buf = io.StringIO()
        with patch.object(auth_mod, "_antigravity_oauth_login", return_value=fake_login), \
             redirect_stdout(buf):
            auth_mod.antigravity_auth_handler("add", type("A", (), {"label": None})())
    finally:
        if saved is not None:
            sys.modules["agent.credential_pool"] = saved
        else:
            sys.modules.pop("agent.credential_pool", None)

    out = buf.getvalue()
    assert "Terms of Service" in out, "the add path must warn about the ToS"
    assert "suspension or termination" in out.lower(), (
        "the warning must state the concrete account risk")
    assert "API key" in out, "the warning must point at the compliant alternative"
