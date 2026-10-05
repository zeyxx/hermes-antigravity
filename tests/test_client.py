import io as _io
import json as _json
import urllib.error
from unittest.mock import patch, MagicMock
from client import AntigravityClient


def test_client_chat_completions_streaming_generator():
    mock_auth = MagicMock()
    mock_auth.get_credentials.return_value = ("fake-token", "fake-project")

    sse_lines = [
        b'data: {"response": {"candidates": [{"content": {"parts": [{"text": "Hello"}]}}]}}\n\n',
        b'data: {"response": {"candidates": [{"content": {"parts": [{"text": " world"}]}, "finishReason": "STOP"}]}}\n\n',
        b"data: [DONE]\n\n",
    ]

    mock_resp = MagicMock()
    mock_resp.__iter__.return_value = sse_lines
    mock_resp.status = 200

    with patch("urllib.request.urlopen", return_value=mock_resp):
        client = AntigravityClient(auth_manager=mock_auth)
        stream = client.chat.completions.create(
            model="gemini-3.8-flash",
            messages=[{"role": "user", "content": "hi"}],
            stream=True,
        )
        chunks = list(stream)
        assert len(chunks) == 2
        assert chunks[0].choices[0]["delta"]["content"] == "Hello"
        assert chunks[1].choices[0]["delta"]["content"] == " world"
        assert chunks[1].choices[0]["finish_reason"] == "stop"


def _http_error(code, payload):
    """A urllib HTTPError whose read() returns a JSON body, as urllib does."""
    err = urllib.error.HTTPError("https://x", code, "err", {}, _io.BytesIO(
        _json.dumps(payload).encode("utf-8")))
    return err


def _client_with_registry():
    """A client on a single endpoint, with an isolated (empty) account registry."""
    import tempfile
    from pathlib import Path
    from accounts import AntigravityAccountRegistry

    sandbox = Path(tempfile.mkdtemp())
    mock_auth = MagicMock()
    mock_auth.get_credentials.return_value = ("fake-token", "fake-project")
    mock_auth.registry = AntigravityAccountRegistry(
        registry_path=sandbox / "accounts.json")
    return AntigravityClient(auth_manager=mock_auth, endpoints=["https://only.example"]), mock_auth


def test_403_validation_required_surfaces_the_verification_link():
    """An unverified Google account must produce an actionable message.

    The relay returns VALIDATION_REQUIRED with a verification URL buried in a
    machine-readable body. Reporting a bare 403 leaves the user with nothing to
    act on; this was observed in the runtime as an opaque failure.
    """
    client, auth = _client_with_registry()
    body = {"error": {"code": 403, "status": "PERMISSION_DENIED", "details": [
        {"reason": "VALIDATION_REQUIRED", "metadata": {
            "validation_url": "https://accounts.google.com/verify/xyz",
            "validation_error_message": "Verify your account to continue.",
        }}]}}

    with patch("urllib.request.urlopen", side_effect=_http_error(403, body)):
        try:
            list(client.generate(model="gemini-3.8-flash", messages=[
                {"role": "user", "content": "hi"}]))
            raise AssertionError("expected a RuntimeError")
        except RuntimeError as exc:
            message = str(exc)
            assert "not verified" in message
            assert "https://accounts.google.com/verify/xyz" in message, (
                "the verification link must be surfaced verbatim")


def test_403_without_validation_details_still_reports_the_code():
    """A 403 with no VALIDATION_REQUIRED payload must not claim a verification issue."""
    client, auth = _client_with_registry()
    with patch("urllib.request.urlopen", side_effect=_http_error(403, {"error": "denied"})):
        try:
            list(client.generate(model="gemini-3.8-flash", messages=[
                {"role": "user", "content": "hi"}]))
            raise AssertionError("expected a RuntimeError")
        except RuntimeError as exc:
            message = str(exc)
            assert "HTTP 403" in message
            assert "not verified" not in message
