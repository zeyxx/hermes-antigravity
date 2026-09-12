"""Antigravity HTTP client adapter matching OpenAI client contract in Hermes AIAgent.

Adapted from Rahul Arya's pi-antigravity (https://github.com/Rahularya01/pi-antigravity, MIT License)
for the Hermes Agent Python runtime.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any, Iterator
try:
    from .auth import AntigravityAuthManager
    from .models import (
        ENDPOINT_FALLBACKS,
        DEFAULT_USER_AGENT,
        resolve_runtime_model,
        clamp_max_tokens,
        resolve_session_trajectory,
        antigravity_request_envelope,
    )
    from .translator import to_antigravity_payload, parse_sse_event, ChatCompletionChunk
except ImportError:
    from auth import AntigravityAuthManager
    from models import (
        ENDPOINT_FALLBACKS,
        DEFAULT_USER_AGENT,
        resolve_runtime_model,
        clamp_max_tokens,
        resolve_session_trajectory,
        antigravity_request_envelope,
    )
    from translator import to_antigravity_payload, parse_sse_event, ChatCompletionChunk

logger = logging.getLogger(__name__)


class _CompletionsAdapter:
    def __init__(self, client: AntigravityClient) -> None:
        self._client = client

    def create(
        self,
        model: str,
        messages: list[dict[str, Any]],
        stream: bool = True,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatCompletionChunk] | Any:
        return self._client.generate(
            model=model,
            messages=messages,
            stream=stream,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )


class _ChatAdapter:
    def __init__(self, client: AntigravityClient) -> None:
        self.completions = _CompletionsAdapter(client)


class AntigravityClient:
    """Client implementing the client.chat.completions interface for Hermes Agent."""

    def __init__(
        self,
        auth_manager: AntigravityAuthManager | None = None,
        endpoints: list[str] | None = None,
    ) -> None:
        self.auth = auth_manager or AntigravityAuthManager()
        self.endpoints = endpoints or ENDPOINT_FALLBACKS
        self.chat = _ChatAdapter(self)

    def generate(
        self,
        model: str,
        messages: list[dict[str, Any]],
        stream: bool = True,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatCompletionChunk] | Any:
        reasoning_effort = kwargs.get("reasoning_effort")
        if not reasoning_effort and isinstance(kwargs.get("extra_body"), dict):
            reasoning_effort = kwargs["extra_body"].get("reasoning_effort")

        runtime_model = resolve_runtime_model(model, reasoning_effort)
        token, project_id = self.auth.get_credentials()

        trajectory = resolve_session_trajectory(messages)
        step = max(1, len(messages))
        request_index = sum(1 for m in messages if m.get("role") == "assistant")
        envelope = antigravity_request_envelope(
            wire_model_id=runtime_model,
            step=step,
            last_step_index=str(max(0, step - 1)),
            request_index=request_index,
            conversation_id=trajectory["conversationId"],
            trajectory_id=trajectory["trajectoryId"],
            is_claude=runtime_model.startswith("claude-"),
        )

        payload = to_antigravity_payload(
            model=runtime_model,
            messages=messages,
            project_id=project_id,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=clamp_max_tokens(runtime_model, max_tokens),
            reasoning_effort=reasoning_effort,
            session_id=envelope["sessionId"],
            labels=envelope["labels"],
            request_id=envelope["requestId"],
        )

        body_bytes = json.dumps(payload).encode("utf-8")

        response = None
        last_error = None

        for endpoint in self.endpoints:
            url = f"{endpoint}/v1internal:streamGenerateContent?alt=sse"
            req = urllib.request.Request(
                url,
                data=body_bytes,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "User-Agent": DEFAULT_USER_AGENT,
                },
                method="POST",
            )
            try:
                response = urllib.request.urlopen(req, timeout=30.0)
                break
            except urllib.error.HTTPError as err:
                last_error = err
                if err.code == 401:
                    # Token expired -> refresh or login
                    try:
                        token = self.auth.refresh_access_token()
                    except Exception:
                        creds = self.auth.login_interactive()
                        token = creds["access_token"]

                    req = urllib.request.Request(
                        url,
                        data=body_bytes,
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/json",
                            "User-Agent": DEFAULT_USER_AGENT,
                        },
                        method="POST",
                    )
                    try:
                        response = urllib.request.urlopen(req, timeout=30.0)
                        break
                    except Exception as retry_exc:
                        last_error = retry_exc
                        continue
                elif err.code == 429:
                    # Rate limited -> exponential backoff then retry same endpoint
                    retry_after = 2
                    logger.warning(
                        "Antigravity 429 rate limit on %s, retrying in %ds",
                        endpoint, retry_after,
                    )
                    time.sleep(retry_after)
                    try:
                        response = urllib.request.urlopen(req, timeout=30.0)
                        break
                    except Exception as retry_exc:
                        last_error = retry_exc
                        continue
                elif err.code in (404, 500, 502, 503, 504):
                    # Candidate endpoint unavailable -> failover
                    continue
                else:
                    err_msg = err.read().decode("utf-8", errors="replace")
                    raise RuntimeError(f"Antigravity API HTTP {err.code}: {err_msg}") from err
            except Exception as exc:
                last_error = exc
                continue

        if response is None:
            raise RuntimeError(f"Antigravity request failed on all endpoints: {last_error}")

        def sse_generator() -> Iterator[ChatCompletionChunk]:
            with response:
                for line in response:
                    decoded = line.decode("utf-8").strip()
                    if not decoded.startswith("data:"):
                        continue
                    payload_part = decoded[len("data:") :].strip()
                    if not payload_part or payload_part == "[DONE]":
                        continue
                    chunks = parse_sse_event(payload_part, model_id=runtime_model)
                    for c in chunks:
                        yield c

        if stream:
            return sse_generator()

        # Non-streaming fallback: collect and aggregate chunks
        all_chunks = list(sse_generator())
        content_parts = []
        reasoning_parts = []
        finish_reason = "stop"
        for chunk in all_chunks:
            for ch in chunk.choices:
                delta = ch.get("delta", {})
                if "content" in delta and delta["content"]:
                    content_parts.append(delta["content"])
                if "reasoning_content" in delta and delta["reasoning_content"]:
                    reasoning_parts.append(delta["reasoning_content"])
                if ch.get("finish_reason"):
                    finish_reason = ch["finish_reason"]

        class _NonStreamingResponse:
            def __init__(self) -> None:
                self.choices = [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "".join(content_parts),
                            "reasoning_content": "".join(reasoning_parts) if reasoning_parts else None,
                        },
                        "finish_reason": finish_reason,
                    }
                ]

        return _NonStreamingResponse()
