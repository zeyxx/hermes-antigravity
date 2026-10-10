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
import uuid
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
        fetch_account_quota,
    )
    from .quota import best_model_row, quota_is_low
    from .translator import (
        to_antigravity_payload,
        parse_sse_event,
        ChatCompletionChunk,
        ChoiceDeltaToolCall,
        ChoiceDeltaToolCallFunction,
        _to_well_formed,
    )
except ImportError:
    from auth import AntigravityAuthManager
    from models import (
        ENDPOINT_FALLBACKS,
        DEFAULT_USER_AGENT,
        resolve_runtime_model,
        clamp_max_tokens,
        resolve_session_trajectory,
        antigravity_request_envelope,
        fetch_account_quota,
    )
    from quota import best_model_row, quota_is_low
    from translator import (
        to_antigravity_payload,
        parse_sse_event,
        ChatCompletionChunk,
        ChoiceDeltaToolCall,
        ChoiceDeltaToolCallFunction,
        _to_well_formed,
    )

logger = logging.getLogger(__name__)


class ChatCompletionMessage:
    def __init__(
        self,
        role: str = "assistant",
        content: str | None = None,
        reasoning_content: str | None = None,
        tool_calls: list[Any] | None = None,
    ) -> None:
        self.role = role
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


class ChatCompletionChoice:
    def __init__(
        self,
        index: int = 0,
        message: ChatCompletionMessage | None = None,
        finish_reason: str | None = "stop",
    ) -> None:
        self.index = index
        self.message = message or ChatCompletionMessage()
        self.finish_reason = finish_reason

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


class CompletionUsage:
    def __init__(
        self,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
    ) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens

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


class ChatCompletionResponse:
    def __init__(
        self,
        id: str,
        model: str,
        choices: list[ChatCompletionChoice],
        usage: CompletionUsage | None = None,
    ) -> None:
        self.id = id
        self.object = "chat.completion"
        self.created = int(time.time())
        self.model = model
        self.choices = choices
        self.usage = usage

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

    def __await__(self):
        async def _identity():
            return self
        return _identity().__await__()


class StreamResponse:
    """Synchronous & asynchronous iterable generator for streaming completions."""

    def __init__(self, generator: Iterator[ChatCompletionChunk], response: Any = None) -> None:
        self._gen = generator
        self._resp = response

    def __iter__(self) -> Iterator[ChatCompletionChunk]:
        return self

    def __next__(self) -> ChatCompletionChunk:
        return next(self._gen)

    def __enter__(self) -> StreamResponse:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._resp is not None and hasattr(self._resp, "close"):
            try:
                self._resp.close()
            except Exception:
                pass

    def __await__(self):
        async def _identity():
            return self
        return _identity().__await__()

    def __aiter__(self):
        async def _agen():
            for chunk in self._gen:
                yield chunk
        return _agen()


class _CompletionsAdapter:
    def __init__(self, client: AntigravityClient) -> None:
        self._client = client

    def create(
        self,
        messages: list[dict[str, Any]] | None = None,
        model: str | None = None,
        stream: bool = False,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> Any:
        return self._client.generate(
            messages=messages or [],
            model=model,
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

    HERMES_SKIP_TRANSPORT_WRAP = True
    HERMES_SKIP_ASYNC_WRAP = True

    def __init__(
        self,
        auth_manager: AntigravityAuthManager | None = None,
        endpoints: list[str] | None = None,
    ) -> None:
        self.auth = auth_manager or AntigravityAuthManager()
        self.endpoints = endpoints or ENDPOINT_FALLBACKS
        self.chat = _ChatAdapter(self)
        # Short-TTL quota snapshot cache. The proactive probe costs up to three
        # RPC round-trips; without caching, every request in a session pays it.
        # Keyed by (account_id, runtime_model); entries older than the TTL are
        # re-fetched so a bucket that just refilled is picked up.
        self._quota_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
        self._quota_cache_ttl = 8.0

    def _get_registry(self):
        """The account registry behind the auth manager, or None.

        The manager stores it as ``_registry`` (private). Reading a public
        ``registry`` attribute here was a latent bug: it is always None, so the
        quota failover never actually fired — the 429 path quietly degraded to
        its backoff. Resolve the real attribute.
        """
        return getattr(self.auth, "_registry", None)

    def _failover_to_next_account(self, tried_account_ids: set[str]) -> str | None:
        """Switch to the next untried linked account after a quota wall.

        Returns the new account's access token when one was activated, so the
        caller can rebuild the request with it. Returns None when there is
        nothing left to try, so the caller falls back to its backoff path.
        """
        registry = self._get_registry()
        if registry is None:
            return None
        try:
            account = registry.next_untried_account(tried_account_ids)
        except Exception as exc:  # registry is optional; never fail the request over it
            logger.debug("account failover unavailable: %s", exc)
            return None
        if account is None:
            logger.warning("Antigravity 429 and no untried linked account remains")
            return None
        logger.warning(
            "Antigravity 429 on the active account; failing over to %s", account.email
        )
        access = account.credentials.get("access_token")
        if not access:
            # Only a refresh token: mint one for THIS account rather than hand
            # back a dead request. ``self.auth`` is still bound to the previous
            # (exhausted) account — its ``refresh_access_token`` would exchange
            # the old refresh token and return the old token, defeating the
            # failover. Build a manager pinned to the new account so the
            # exchange and persistence target the account we just activated.
            try:
                access = self._refresh_for_account(account)
            except Exception as exc:
                logger.warning("failover to %s could not refresh: %s", account.email, exc)
                return None
        return str(access)

    def _refresh_for_account(self, account) -> str:
        """Exchange a specific account's refresh token for a fresh access token.

        Pins a new auth manager to ``account`` (not the active one) so the OAuth
        exchange uses that account's refresh token and persists the result back
        to its registry entry. Falls back to the active manager only when
        ``account`` already carries a usable token.
        """
        manager = AntigravityAuthManager(
            registry=self._get_registry(),
            account_id=account.account_id,
            auto_migrate=False,
        )
        return manager.refresh_access_token()

    def _cached_quota(
        self,
        account_id: str,
        runtime_model: str,
        token: str,
        creds: dict[str, Any],
        email: str | None,
    ) -> dict[str, Any]:
        """Fetch the account's quota snapshot, or return a fresh cached one.

        The proactive probe costs up to three RPC round-trips (up to ~15s).
        Caching for a few seconds collapses the per-request cost to a single
        probe per (account, model) window while keeping a just-refilled bucket
        visible within the TTL. A cache read never fails the request: on any
        error it falls through to a live fetch.
        """
        now = time.monotonic()
        key = (account_id, runtime_model)
        cached = self._quota_cache.get(key)
        if cached is not None and (now - cached[0]) < self._quota_cache_ttl:
            return cached[1]
        snapshot = fetch_account_quota(
            token,
            creds.get("project_id", "antigravity-default"),
            timeout=5.0,
            email=email,
        )
        self._quota_cache[key] = (now, snapshot)
        return snapshot

    def _switch_account_if_quota_low(
        self, runtime_model: str, tried_account_ids: set[str]
    ) -> str | None:
        """Proactively switch accounts when the active one's bucket is empty.

        Reactive 429 failover is a safety net, not a strategy: it only fires
        after a request has already failed, burning a round-trip and the
        backoff budget on a wall the quota API reports for free. This reads the
        per-model quota first and moves off an exhausted account before sending.

        Guarded on purpose:
        - it only runs with two or more linked accounts, because switching to
          the same account is not a switch and a lone account has nowhere to go;
        - it only counts an *empty* bucket (``remainingFraction`` at/below the
          threshold, or ``isExhausted``) as low, so a thin-but-usable bucket
          still gets its request and the 429 path remains the backstop;
        - any failure to read quota is swallowed and returns None, so a flaky
          measurement never blocks a request that would have succeeded.
        """
        registry = self._get_registry()
        if registry is None:
            return None
        try:
            if registry.account_count < 2:
                return None
        except Exception:
            return None

        try:
            active_id = registry.active_account_id
            if active_id:
                tried_account_ids.add(active_id)
            account = registry.get_account(active_id)
            if account is None:
                return None
            creds = account.credentials or {}
            token = creds.get("access_token")
            if not token:
                return None
            snapshot = self._cached_quota(
                account.account_id, runtime_model, token, creds, account.email
            )
        except Exception as exc:  # never fail a request over a quota probe
            logger.debug("proactive quota probe unavailable: %s", exc)
            return None

        row = best_model_row(snapshot.get("models", []), runtime_model)
        if not quota_is_low(row):
            return None

        logger.info(
            "Antigravity quota empty for %s on %s; switching account before request",
            runtime_model, account.email,
        )
        return self._failover_to_next_account(tried_account_ids)

    def generate(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        stream: bool = False,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> Any:
        reasoning_effort = kwargs.get("reasoning_effort")
        if not reasoning_effort and isinstance(kwargs.get("extra_body"), dict):
            reasoning_effort = kwargs["extra_body"].get("reasoning_effort")

        runtime_model = resolve_runtime_model(model, reasoning_effort)
        token, project_id = self.auth.get_credentials()

        # Track accounts already tried this request, and — before spending a
        # round-trip — move off an account whose quota for this model is already
        # empty. Reactive 429 failover stays as the backstop; this avoids the
        # failed request that would trigger it.
        tried_accounts: set[str] = set()
        registry = self._get_registry()
        active_id = None
        if registry is not None:
            active_id = registry.active_account_id
        if active_id:
            tried_accounts.add(active_id)
        switched_token = self._switch_account_if_quota_low(runtime_model, tried_accounts)
        if switched_token:
            token = switched_token
            if registry is not None:
                new_active = registry.active_account_id
                if new_active:
                    tried_accounts.add(new_active)
                    # Read the project id from the NEWLY activated account, not
                    # from ``self.auth`` — the manager is still bound to the
                    # previous account, so its project_id is stale. Sending the
                    # new account's token with the old project mismatches and
                    # 404s or misattributes quota.
                    new_account = registry.get_account(new_active)
                    if new_account is not None:
                        new_project = (new_account.credentials or {}).get("project_id")
                        if new_project:
                            project_id = new_project

        trajectory = resolve_session_trajectory(messages, kwargs.get("session_id"))
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

        body_bytes = json.dumps(_to_well_formed(payload)).encode("utf-8")

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
                    # Rate limited -> try another linked account before sleeping.
                    # A 429 from Antigravity is usually a per-account quota wall,
                    # so retrying the same token just burns the backoff budget.
                    new_token = self._failover_to_next_account(tried_accounts)
                    if new_token:
                        token = new_token
                        current = self._get_registry()
                        if current is not None:
                            switched = current.get_account()
                            if switched is not None:
                                tried_accounts.add(switched.account_id)
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
                        continue
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
                elif err.code == 403:
                    # 403 VALIDATION_REQUIRED means the Google account is unverified.
                    # The relay buries the fix in a machine-readable error body, so
                    # surface the verification link instead of a bare 403.
                    err_body = ""
                    try:
                        err_body = err.read().decode("utf-8", errors="replace")
                        err_data = json.loads(err_body)
                    except Exception:
                        err_data = {}

                    validation_url = None
                    validation_msg = (
                        "Your Google account must be verified before using Antigravity."
                    )
                    # Google nests the detail list under "error"; tolerate both shapes.
                    payload = err_data.get("error") if isinstance(err_data.get("error"), dict) else err_data
                    details = payload.get("details", [])
                    if isinstance(details, list):
                        for detail in details:
                            if isinstance(detail, dict) and detail.get("reason") == "VALIDATION_REQUIRED":
                                meta = detail.get("metadata", {})
                                if isinstance(meta, dict):
                                    validation_url = meta.get("validation_url")
                                    validation_msg = meta.get(
                                        "validation_error_message", validation_msg
                                    )
                                break

                    if validation_url:
                        raise RuntimeError(
                            f"Google account not verified. {validation_msg}\n"
                            "Open this link in a browser to verify your account:\n"
                            f"  -> {validation_url}\n"
                            "Then re-run your command."
                        ) from err
                    err_msg = err_body or "no body"
                    raise RuntimeError(f"Antigravity API HTTP {err.code}: {err_msg}") from err
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
                    yield from chunks

        if stream:
            return StreamResponse(sse_generator(), response=response)

        # Non-streaming: collect and aggregate chunks into a ChatCompletionResponse
        all_chunks = list(sse_generator())
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls_map: dict[str, dict[str, Any]] = {}
        finish_reason = "stop"
        usage_obj: CompletionUsage | None = None

        for chunk in all_chunks:
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage and isinstance(chunk_usage, dict):
                usage_obj = CompletionUsage(
                    prompt_tokens=chunk_usage.get("prompt_tokens", 0),
                    completion_tokens=chunk_usage.get("completion_tokens", 0),
                    total_tokens=chunk_usage.get("total_tokens", 0),
                )
            for ch in chunk.choices:
                delta = getattr(ch, "delta", None) or (ch.get("delta", {}) if isinstance(ch, dict) else {})
                c = getattr(delta, "content", None) if not isinstance(delta, dict) else delta.get("content")
                if c:
                    content_parts.append(c)
                r = getattr(delta, "reasoning_content", None) if not isinstance(delta, dict) else delta.get("reasoning_content")
                if r:
                    reasoning_parts.append(r)
                tcs = getattr(delta, "tool_calls", None) if not isinstance(delta, dict) else delta.get("tool_calls")
                if tcs:
                    for tc in tcs:
                        tc_id = getattr(tc, "id", None) if not isinstance(tc, dict) else tc.get("id")
                        fn = getattr(tc, "function", None) if not isinstance(tc, dict) else tc.get("function")
                        fn_name = getattr(fn, "name", "") if not isinstance(fn, dict) else fn.get("name", "")
                        fn_args = getattr(fn, "arguments", "") if not isinstance(fn, dict) else fn.get("arguments", "")
                        if tc_id and tc_id in tool_calls_map:
                            tool_calls_map[tc_id]["function"]["arguments"] += fn_args
                        elif tc_id:
                            tool_calls_map[tc_id] = {
                                "id": tc_id,
                                "type": "function",
                                "function": {"name": fn_name, "arguments": fn_args},
                            }
                fr = getattr(ch, "finish_reason", None) if not isinstance(ch, dict) else ch.get("finish_reason")
                if fr:
                    finish_reason = fr

        final_tool_calls: list[ChoiceDeltaToolCall] | None = None
        if tool_calls_map:
            final_tool_calls = [
                ChoiceDeltaToolCall(
                    index=i,
                    id=data["id"],
                    type="function",
                    function=ChoiceDeltaToolCallFunction(
                        name=data["function"]["name"],
                        arguments=data["function"]["arguments"],
                    ),
                )
                for i, data in enumerate(tool_calls_map.values())
            ]
            if finish_reason == "stop":
                finish_reason = "tool_calls"

        choice = ChatCompletionChoice(
            index=0,
            message=ChatCompletionMessage(
                role="assistant",
                content="".join(content_parts) if content_parts else None,
                reasoning_content="".join(reasoning_parts) if reasoning_parts else None,
                tool_calls=final_tool_calls,
            ),
            finish_reason=finish_reason,
        )

        return ChatCompletionResponse(
            id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
            model=runtime_model,
            choices=[choice],
            usage=usage_obj,
        )
