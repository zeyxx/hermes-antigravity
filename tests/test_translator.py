import json
from translator import (
    to_antigravity_payload,
    parse_sse_event,
    _THOUGHT_SIGNATURES,
)

def test_to_antigravity_payload_messages_and_tools():
    messages = [
        {"role": "system", "content": "You are a helpful coding agent."},
        {"role": "user", "content": "Write hello world in python"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {"name": "run_shell", "arguments": '{"cmd": "echo 1"}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_123",
            "content": "1\n",
        },
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "run_shell",
                "description": "Execute a shell command",
                "parameters": {
                    "type": "object",
                    "properties": {"cmd": {"type": "string"}},
                    "required": ["cmd"],
                },
            },
        }
    ]

    _THOUGHT_SIGNATURES["call_123"] = "dummy_sig_123"

    payload = to_antigravity_payload(
        model="gemini-3.8-flash",
        messages=messages,
        project_id="proj-test",
        tools=tools,
        reasoning_effort="medium",
    )

    assert payload["project"] == "proj-test"
    assert payload["model"] == "gemini-3.8-flash"
    assert "request" in payload

    req = payload["request"]
    assert "systemInstruction" in req
    assert req["systemInstruction"]["parts"][0]["text"] == "You are a helpful coding agent."

    contents = req["contents"]
    assert len(contents) == 3
    assert contents[0]["role"] == "user"
    assert contents[1]["role"] == "model"
    assert "functionCall" in contents[1]["parts"][0]
    assert contents[1]["parts"][0]["functionCall"]["name"] == "run_shell"
    assert contents[2]["role"] == "user"
    assert "functionResponse" in contents[2]["parts"][0]

    assert "tools" in req
    assert len(req["tools"][0]["functionDeclarations"]) == 1
    assert req["tools"][0]["functionDeclarations"][0]["name"] == "run_shell"


def test_unsigned_tool_call_falls_back_to_text_observation():
    _THOUGHT_SIGNATURES.clear()
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_unsigned",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "foo.txt"}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_unsigned",
            "content": "file content here",
        },
    ]
    payload = to_antigravity_payload(
        model="gemini-3.8-flash",
        messages=messages,
        project_id="proj-test",
    )
    contents = payload["request"]["contents"]
    assert len(contents) == 2
    assert "[Action: invoked read_file" in contents[0]["parts"][0]["text"]
    assert "[Observation from read_file" in contents[1]["parts"][0]["text"]

def test_parse_sse_event_text_and_thinking():
    data = {
        "response": {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "Thinking step...", "thought": True},
                            {"text": "Final answer."},
                        ]
                    },
                    "finishReason": "STOP",
                }
            ]
        }
    }
    raw_event = json.dumps(data)
    chunks = parse_sse_event(raw_event, model_id="gemini-3.8-flash")
    assert len(chunks) == 2

    # Thinking chunk
    assert chunks[0].choices[0]["delta"].get("reasoning_content") == "Thinking step..."
    assert "content" not in chunks[0].choices[0]["delta"]

    # Text chunk
    assert chunks[1].choices[0]["delta"].get("content") == "Final answer."
    assert chunks[1].choices[0].get("finish_reason") == "stop"


def test_parse_sse_event_function_call():
    data = {
        "response": {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "functionCall": {
                                    "id": "call_abc",
                                    "name": "bash",
                                    "args": {"command": "ls"},
                                }
                            }
                        ]
                    },
                    "finishReason": "STOP",
                }
            ]
        }
    }
    raw_event = json.dumps(data)
    chunks = parse_sse_event(raw_event, model_id="gemini-3.8-flash")
    assert len(chunks) == 1
    delta = chunks[0].choices[0]["delta"]
    assert "tool_calls" in delta
    tc = delta["tool_calls"][0]
    assert tc["function"]["name"] == "bash"
    assert json.loads(tc["function"]["arguments"]) == {"command": "ls"}


def test_payload_carries_session_envelope():
    payload = to_antigravity_payload(
        model="gemini-3.8-flash-low",
        messages=[{"role": "user", "content": "hi"}],
        project_id="p",
        session_id="conv-1",
        labels={"antigravity/model": "gemini_3.8_flash_low"},
        request_id="traj-1-0-1",
    )
    # sessionId/labels nest INSIDE request (top-level => Google HTTP 400)
    assert payload["request"]["sessionId"] == "conv-1"
    assert payload["request"]["labels"] == {"antigravity/model": "gemini_3.8_flash_low"}
    assert payload["requestId"] == "traj-1-0-1"
    assert "sessionId" not in payload
    assert "labels" not in payload


def test_payload_without_envelope_omits_session_fields():
    payload = to_antigravity_payload(
        model="gemini-3.8-flash-low",
        messages=[{"role": "user", "content": "hi"}],
        project_id="p",
    )
    assert "sessionId" not in payload
    assert "labels" not in payload
    assert payload["requestId"]


def test_function_response_uses_output_shape():
    """Upstream pi-antigravity uses response.output; response.content is non-canonical."""
    _THOUGHT_SIGNATURES["__last__"] = "sig-test"
    try:
        payload = to_antigravity_payload(
            model="gemini-3.8-flash-low",
            messages=[
                {"role": "user", "content": "calc"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "call_1", "type": "function",
                         "function": {"name": "calc", "arguments": "{}"}},
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "42"},
            ],
            project_id="p",
        )
    finally:
        _THOUGHT_SIGNATURES.pop("__last__", None)
    fr = payload["request"]["contents"][-1]["parts"][0]["functionResponse"]
    assert fr["response"] == {"output": "42"}
    assert fr["id"] == "call_1"


def test_claude_function_call_includes_id():
    """Claude/GPT-OSS require the id field on functionCall (Google 400s without it)."""
    _THOUGHT_SIGNATURES.clear()
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "toolu_abc123",
                    "type": "function",
                    "function": {"name": "bash", "arguments": '{"cmd": "ls"}'},
                }
            ],
        },
    ]
    payload = to_antigravity_payload(
        model="claude-sonnet-4-6",
        messages=messages,
        project_id="proj-test",
    )
    fc = payload["request"]["contents"][0]["parts"][0]["functionCall"]
    assert "id" in fc, "Claude functionCall must include id"
    assert fc["id"] == "toolu_abc123"


def test_claude_function_response_includes_id():
    """Claude/GPT-OSS require the id field on functionResponse."""
    _THOUGHT_SIGNATURES.clear()
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "toolu_abc123",
                    "type": "function",
                    "function": {"name": "bash", "arguments": '{"cmd": "ls"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "toolu_abc123", "content": "file.txt"},
    ]
    payload = to_antigravity_payload(
        model="claude-sonnet-4-6",
        messages=messages,
        project_id="proj-test",
    )
    fr = payload["request"]["contents"][1]["parts"][0]["functionResponse"]
    assert "id" in fr, "Claude functionResponse must include id"
    assert fr["id"] == "toolu_abc123"


def test_sanitize_schema_strips_unsupported_keywords():
    """Claude strictly validates tool schemas: anyOf/oneOf/allOf must go (issue #2)."""
    from translator import _sanitize_schema
    schema = {
        "type": "object",
        "properties": {
            # Exact shape from the issue: terminal.notify
            "notify": {"anyOf": [{"type": "boolean"}, {"type": "array", "items": {"type": "string"}}]},
            "cmd": {"type": "string", "description": "command"},
            "nested": {"type": "object", "properties": {"x": {"oneOf": [{"type": "string"}]}}},
        },
        "required": ["cmd"],
    }
    clean = _sanitize_schema(schema)
    assert "anyOf" not in clean["properties"]["notify"]
    assert clean["properties"]["cmd"] == {"type": "string", "description": "command"}
    assert "oneOf" not in clean["properties"]["nested"]["properties"]["x"]
    assert clean["required"] == ["cmd"]
    # Lists recurse too
    assert _sanitize_schema([{"anyOf": [1], "type": "string"}]) == [{"type": "string"}]
    assert _sanitize_schema("scalar") == "scalar"


def test_tool_declaration_schema_is_sanitized():
    """End-to-end: declared tool parameters carry no anyOf/oneOf."""
    payload = to_antigravity_payload(
        model="claude-sonnet-4-6",
        messages=[{"role": "user", "content": "hi"}],
        project_id="p",
        tools=[{"type": "function", "function": {
            "name": "terminal",
            "description": "run",
            "parameters": {"type": "object",
                           "properties": {"notify": {"anyOf": [{"type": "boolean"}]}},
                           "allOf": [{"type": "object"}]},
        }}],
    )
    params = payload["request"]["tools"][0]["functionDeclarations"][0]["parameters"]
    assert "anyOf" not in params["properties"]["notify"]
    assert "allOf" not in params
