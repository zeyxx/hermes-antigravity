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
