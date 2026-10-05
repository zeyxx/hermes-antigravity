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
    import errno
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
    assert "deja utilise" in output or "deja utilisé" in output, (
        f"the message must say the port is in use; got: {output!r}")
