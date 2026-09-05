"""Bidirectional translator between Hermes OpenAI structures and Google Antigravity payloads.

Adapted from Rahul Arya's pi-antigravity (https://github.com/Rahularya01/pi-antigravity, MIT License)
for the Hermes Agent Python runtime.
"""
import json
import uuid
from typing import Any
try:
    from .models import get_thinking_config
except ImportError:
    from models import get_thinking_config

_THOUGHT_SIGNATURES: dict[str, str] = {}



class ChoiceDeltaToolCallFunction:
    def __init__(self, name: str = "", arguments: str = "") -> None:
        self.name = name
        self.arguments = arguments

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


class ChoiceDeltaToolCall:
    def __init__(
        self,
        index: int = 0,
        id: str = "",
        type: str = "function",
        function: ChoiceDeltaToolCallFunction | None = None,
    ) -> None:
        self.index = index
        self.id = id
        self.type = type
        self.function = function or ChoiceDeltaToolCallFunction()

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

class ChoiceDelta:
    def __init__(
        self,
        content: str | None = None,
        reasoning_content: str | None = None,
        tool_calls: list[Any] | None = None,
    ) -> None:
        self.content = content
        self.reasoning_content = reasoning_content
        self.tool_calls = tool_calls

    def __getitem__(self, key: str) -> Any:
        val = getattr(self, key, None)
        if val is not None:
            return val
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        val = getattr(self, key, None)
        return val if val is not None else default

    def __contains__(self, key: str) -> bool:
        return getattr(self, key, None) is not None


class ChunkChoice:
    def __init__(
        self,
        index: int = 0,
        delta: ChoiceDelta | None = None,
        finish_reason: str | None = None,
    ) -> None:
        self.index = index
        self.delta = delta or ChoiceDelta()
        self.finish_reason = finish_reason

    def __getitem__(self, key: str) -> Any:
        val = getattr(self, key, None)
        if val is not None:
            return val
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        val = getattr(self, key, None)
        return val if val is not None else default


class ChatCompletionChunk:
    """Mock OpenAI ChatCompletionChunk compatible with Hermes AIAgent."""

    def __init__(
        self,
        chunk_id: str,
        model: str,
        choices: list[ChunkChoice],
        usage: dict[str, Any] | None = None,
    ) -> None:
        self.id = chunk_id
        self.object = "chat.completion.chunk"
        self.model = model
        self.choices = choices
        self.usage = usage

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "object": self.object,
            "model": self.model,
            "choices": [
                {
                    "index": c.index,
                    "delta": {
                        k: getattr(c.delta, k)
                        for k in ("content", "reasoning_content", "tool_calls")
                        if getattr(c.delta, k) is not None
                    },
                    "finish_reason": c.finish_reason,
                }
                for c in self.choices
            ],
            "usage": self.usage,
        }

def to_antigravity_payload(
    model: str,
    messages: list[dict[str, Any]],
    project_id: str,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: Any = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Convert Hermes messages and parameters into Google Antigravity wire payload."""
    contents: list[dict[str, Any]] = []
    system_parts: list[dict[str, str]] = []

    tool_call_names: dict[str, str] = {}
    unsigned_tool_ids: set[str] = set()
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")

        if role in ("system", "developer"):
            if isinstance(content, str) and content.strip():
                system_parts.append({"text": content})
            continue

        if role == "user":
            parts = []
            if isinstance(content, str):
                parts.append({"text": content})
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, str):
                        parts.append({"text": item})
                    elif isinstance(item, dict):
                        if item.get("type") == "text":
                            parts.append({"text": item.get("text", "")})
                        elif item.get("type") == "image_url":
                            url = item.get("image_url", {}).get("url", "")
                            if url.startswith("data:"):
                                mime_data = url.split(";base64,")
                                mime = mime_data[0].replace("data:", "")
                                b64 = mime_data[1] if len(mime_data) > 1 else ""
                                parts.append({"inlineData": {"mimeType": mime, "data": b64}})
            contents.append({"role": "user", "parts": parts or [{"text": ""}]})

        elif role == "assistant":
            parts = []
            if content and isinstance(content, str):
                parts.append({"text": content})

            tool_calls = msg.get("tool_calls") or []
            for tc in tool_calls:
                fn = tc.get("function", {})
                fn_name = fn.get("name", "tool")
                tc_id = tc.get("id", f"call_{uuid.uuid4().hex[:8]}")
                tool_call_names[tc_id] = fn_name

                raw_args = fn.get("arguments", "{}")
                args_dict = json.loads(raw_args) if isinstance(raw_args, str) else raw_args

                sig = _THOUGHT_SIGNATURES.get(tc_id) or _THOUGHT_SIGNATURES.get(fn_name) or _THOUGHT_SIGNATURES.get("__last__")
                is_gemini_3 = any(m in model for m in ("gemini-3", "gemini-2.5"))
                if is_gemini_3 and not sig:
                    unsigned_tool_ids.add(tc_id)
                    parts.append({"text": f"[Action: invoked {fn_name} with {json.dumps(args_dict)}]"})
                else:
                    fc_body: dict[str, Any] = {
                        "name": fn_name,
                        "args": args_dict or {},
                    }
                    fc_part: dict[str, Any] = {"functionCall": fc_body}
                    if sig:
                        fc_part["thoughtSignature"] = sig
                    parts.append(fc_part)
            contents.append({"role": "model", "parts": parts or [{"text": ""}]})

        elif role == "tool":
            tc_id = msg.get("tool_call_id", "")
            fn_name = tool_call_names.get(tc_id, "tool")
            res_content = content if isinstance(content, str) else json.dumps(content)
            if tc_id in unsigned_tool_ids:
                contents.append(
                    {
                        "role": "user",
                        "parts": [{"text": f"[Observation from {fn_name}:\n{res_content}]"}],
                    }
                )
            else:
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": fn_name,
                                    "response": {"content": res_content},
                                }
                            }
                        ],
                    }
                )
    request_body: dict[str, Any] = {"contents": contents}

    if system_parts:
        request_body["systemInstruction"] = {
            "role": "user",
            "parts": system_parts,
        }

    # Generation config
    gen_config: dict[str, Any] = {}
    if temperature is not None:
        gen_config["temperature"] = temperature
    if max_tokens is not None:
        gen_config["maxOutputTokens"] = max_tokens

    thinking = get_thinking_config(model, reasoning_effort)
    if thinking:
        gen_config["thinkingConfig"] = thinking

    if gen_config:
        request_body["generationConfig"] = gen_config

    # Tools conversion
    if tools:
        declarations = []
        for t in tools:
            if t.get("type") == "function":
                fn = t.get("function", {})
                declarations.append(
                    {
                        "name": fn.get("name"),
                        "description": fn.get("description", ""),
                        "parameters": fn.get("parameters", {}),
                    }
                )
        if declarations:
            request_body["tools"] = [{"functionDeclarations": declarations}]

    req_id = f"{uuid.uuid4().hex[:12]}-0"

    return {
        "project": project_id,
        "model": model,
        "request": request_body,
        "requestType": "agent",
        "userAgent": "antigravity",
        "requestId": req_id,
    }


def parse_sse_event(json_str: str, model_id: str) -> list[ChatCompletionChunk]:
    """Parse one SSE data JSON payload into ChatCompletionChunk objects."""
    chunks: list[ChatCompletionChunk] = []
    try:
        data = json.loads(json_str)
    except Exception:
        return chunks

    resp = data.get("response") or data
    candidates = resp.get("candidates") or []
    if not candidates:
        return chunks

    candidate = candidates[0]
    parts = candidate.get("content", {}).get("parts", [])
    finish_reason_raw = candidate.get("finishReason")
    finish_reason = (
        "stop"
        if finish_reason_raw == "STOP"
        else "length"
        if finish_reason_raw == "MAX_TOKENS"
        else None
    )

    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

    for idx, part in enumerate(parts):
        content = None
        reasoning_content = None
        tool_calls = None
        is_last = idx == len(parts) - 1

        sig = part.get("thoughtSignature")
        if sig:
            _THOUGHT_SIGNATURES["__last__"] = sig

        if "text" in part:
            if part.get("thought") is True:
                reasoning_content = part["text"]
            else:
                content = part["text"]

        if "functionCall" in part:
            fc = part["functionCall"]
            tc_id = fc.get("id") or f"call_{uuid.uuid4().hex[:8]}"
            if sig:
                _THOUGHT_SIGNATURES[tc_id] = sig
                if fc.get("name"):
                    _THOUGHT_SIGNATURES[fc["name"]] = sig
            args_str = (
                json.dumps(fc.get("args", {}))
                if isinstance(fc.get("args"), dict)
                else str(fc.get("args", "{}"))
            )
            fn_obj = ChoiceDeltaToolCallFunction(
                name=fc.get("name", ""),
                arguments=args_str,
            )
            tool_calls = [
                ChoiceDeltaToolCall(
                    index=0,
                    id=tc_id,
                    type="function",
                    function=fn_obj,
                )
            ]
            if is_last and not finish_reason:
                finish_reason = "tool_calls"
        if content is not None or reasoning_content is not None or tool_calls is not None:
            delta = ChoiceDelta(
                content=content,
                reasoning_content=reasoning_content,
                tool_calls=tool_calls,
            )
            choice = ChunkChoice(
                index=0,
                delta=delta,
                finish_reason=finish_reason if is_last else None,
            )
            chunks.append(ChatCompletionChunk(chunk_id=chunk_id, model=model_id, choices=[choice]))

    return chunks
