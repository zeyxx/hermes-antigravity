import json
from translator import (
    to_antigravity_payload,
    parse_sse_event,
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
