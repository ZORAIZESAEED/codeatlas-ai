"""Ollama adapter (local models over the Ollama HTTP API). Nothing leaves the machine.

Uses ``/api/chat`` with NDJSON streaming. ``num_ctx`` is always sent: Ollama's default context is
small and it silently truncates longer prompts, which would drop source evidence.
"""
import json
import time
from collections.abc import Iterator
from typing import Any

import httpx

from app.services.llm.base import (
    CancelToken,
    Capabilities,
    Deadline,
    Generation,
    LLMError,
    Message,
    ProviderStatus,
    StreamDelta,
    ToolCall,
    ToolSpec,
    Usage,
)

START_HINT = (
    "Start Ollama (run `ollama serve`, or open the Ollama app) and check that it listens on {url}."
)


class OllamaProvider:
    name = "ollama"

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        context_tokens: int,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.context_tokens = context_tokens
        self.timeout_seconds = timeout_seconds
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout_seconds, connect=5.0),
            transport=transport,
            follow_redirects=False,
        )
        self._show: dict[str, Any] | None = None

    def close(self) -> None:
        self._client.close()

    # -- metadata -------------------------------------------------------------------------
    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            return self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise LLMError("timeout", f"Ollama did not respond at {self.base_url}.") from exc
        except httpx.HTTPError as exc:
            raise LLMError(
                "unavailable",
                f"Cannot reach Ollama at {self.base_url}. " + START_HINT.format(url=self.base_url),
            ) from exc

    def _model_info(self) -> dict[str, Any]:
        if self._show is None:
            response = self._request("POST", "/api/show", json={"model": self.model})
            if response.status_code == 404:
                raise LLMError(
                    "model_missing",
                    f"The Ollama model '{self.model}' is not installed. "
                    f"Run `ollama pull {self.model}` or set LLM_MODEL to an installed model.",
                )
            if response.status_code != 200:
                raise LLMError("provider_error", f"Ollama /api/show failed (HTTP "
                                                 f"{response.status_code}).")
            try:
                self._show = response.json()
            except ValueError as exc:
                raise LLMError("invalid_response", "Ollama returned invalid JSON.") from exc
        return self._show

    def capabilities(self) -> Capabilities:
        try:
            advertised = "tools" in (self._model_info().get("capabilities") or [])
        except LLMError:
            advertised = False
        # "advertised": the model metadata claims tool support, but some models still answer
        # with tool-call-shaped text instead of a structured call; callers verify every reply.
        return Capabilities(streaming=True, native_tools="advertised" if advertised else "no",
                            json_output=True, usage=True)

    def context_length(self) -> int | None:
        info = self._model_info().get("model_info") or {}
        for key, value in info.items():
            if key.endswith(".context_length") and isinstance(value, int):
                return value
        return None

    def status(self) -> ProviderStatus:
        response = self._request("GET", "/api/tags")
        if response.status_code != 200:
            raise LLMError("provider_error", f"Ollama /api/tags failed (HTTP "
                                             f"{response.status_code}).")
        try:
            installed = sorted(m["name"] for m in response.json().get("models", []))
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMError("invalid_response", "Ollama returned an invalid model list.") from exc
        status = ProviderStatus(
            provider=self.name, model=self.model, available=False, message="",
            installed_models=installed,
        )
        try:
            self._model_info()
        except LLMError as exc:
            status.message = exc.message
            return status
        status.available = True
        status.capabilities = self.capabilities()
        status.context_length = self.context_length()
        status.message = f"Ollama is running and '{self.model}' is installed."
        if status.context_length and self.context_tokens > status.context_length:
            status.message += (
                f" LLM_MAX_CONTEXT_TOKENS ({self.context_tokens}) exceeds the model's context "
                f"length ({status.context_length}); the model's limit is used."
            )
        return status

    # -- generation -----------------------------------------------------------------------
    @staticmethod
    def _wire_messages(system: str, messages: list[Message]) -> list[dict[str, Any]]:
        wire: list[dict[str, Any]] = [{"role": "system", "content": system}] if system else []
        for m in messages:
            item: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.tool_calls:
                item["tool_calls"] = [
                    {"function": {"name": c.name, "arguments": c.arguments}} for c in m.tool_calls
                ]
            if m.role == "tool" and m.tool_name:
                item["tool_name"] = m.tool_name
            wire.append(item)
        return wire

    @staticmethod
    def _parse_tool_calls(raw: Any) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for item in raw or []:
            fn = item.get("function") if isinstance(item, dict) else None
            if not isinstance(fn, dict) or not isinstance(fn.get("name"), str):
                raise LLMError("invalid_response", "Ollama returned a malformed tool call.")
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError as exc:
                    raise LLMError("invalid_response",
                                   "Ollama returned tool arguments that are not JSON.") from exc
            if not isinstance(args, dict):
                raise LLMError("invalid_response", "Ollama returned non-object tool arguments.")
            calls.append(ToolCall(name=fn["name"], arguments=args, id=item.get("id")))
        return calls

    def stream(
        self,
        messages: list[Message],
        *,
        system: str,
        tools: list[ToolSpec] | None = None,
        max_tokens: int,
        temperature: float,
        cancel: CancelToken | None = None,
    ) -> Iterator[StreamDelta]:
        started = time.perf_counter()
        deadline = Deadline(self.timeout_seconds)
        context = self.context_tokens
        try:
            limit = self.context_length()
            if limit:
                context = min(context, limit)
        except LLMError as exc:
            if exc.code in ("model_missing", "unavailable"):
                raise
        body: dict[str, Any] = {
            "model": self.model,
            "messages": self._wire_messages(system, messages),
            "stream": True,
            "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": context},
        }
        if tools:
            body["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description,
                                                  "parameters": t.parameters}}
                for t in tools
            ]
        text: list[str] = []
        calls: list[ToolCall] = []
        unregister = lambda: None  # noqa: E731
        if cancel is not None:
            cancel.raise_if_cancelled()
            # Ollama sends response headers only with the first token, and prompt processing can
            # take minutes on CPU. Closing the client aborts the in-flight connection at once
            # (Ollama then stops the job). Providers are created per chat turn, so a cancelled
            # turn never reuses this client.
            unregister = cancel.on_cancel(self._client.close)
        try:
            with self._client.stream("POST", "/api/chat", json=body) as response:
                if response.status_code == 404:
                    raise LLMError("model_missing",
                                   f"The Ollama model '{self.model}' is not installed. "
                                   f"Run `ollama pull {self.model}`.")
                if response.status_code != 200:
                    response.read()
                    raise LLMError("provider_error",
                                   f"Ollama returned HTTP {response.status_code}: "
                                   f"{_error_text(response)}")
                for line in response.iter_lines():
                    if cancel is not None:
                        cancel.raise_if_cancelled()
                    deadline.check("The Ollama request")
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except ValueError as exc:
                        raise LLMError("invalid_response",
                                       "Ollama streamed a line that is not JSON.") from exc
                    if not isinstance(chunk, dict):
                        raise LLMError("invalid_response", "Ollama streamed an invalid item.")
                    if chunk.get("error"):
                        raise LLMError("provider_error", f"Ollama error: {chunk['error']}")
                    message = chunk.get("message") or {}
                    piece = message.get("content") or ""
                    if piece:
                        text.append(piece)
                        yield StreamDelta(text=piece)
                    calls.extend(self._parse_tool_calls(message.get("tool_calls")))
                    if chunk.get("done"):
                        yield StreamDelta(done=True, generation=Generation(
                            text="".join(text), tool_calls=calls,
                            usage=Usage(chunk.get("prompt_eval_count"), chunk.get("eval_count")),
                            model=self.model, provider=self.name,
                            finish_reason=chunk.get("done_reason"),
                            duration_ms=int((time.perf_counter() - started) * 1000),
                        ))
                        return
        except httpx.TimeoutException as exc:
            raise LLMError("timeout", f"Ollama did not respond within "
                                      f"{self.timeout_seconds:g} s.") from exc
        except (httpx.HTTPError, httpx.StreamError, OSError) as exc:
            if cancel is not None and cancel.cancelled:
                raise LLMError("cancelled", "Generation was cancelled.") from exc
            raise LLMError("unavailable", f"Lost connection to Ollama at {self.base_url}. "
                           + START_HINT.format(url=self.base_url)) from exc
        finally:
            unregister()
        if cancel is not None and cancel.cancelled:
            raise LLMError("cancelled", "Generation was cancelled.")
        raise LLMError("invalid_response", "Ollama closed the stream before it was complete.")


def _error_text(response: httpx.Response) -> str:
    try:
        return str(response.json().get("error", ""))[:300]
    except ValueError:
        return response.text[:300]
