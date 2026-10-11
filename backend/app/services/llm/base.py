"""Provider-neutral chat model interface (Milestone 4).

Providers are created per request and contacted only when a chat request needs them; nothing is
loaded at application startup. Capabilities are reported, never assumed: a provider that cannot
return native tool calls says so, and callers fall back to deterministic logic.
"""
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema object


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str | None = None


@dataclass
class Message:
    role: Role
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_name: str | None = None  # for role="tool"


@dataclass
class Usage:
    """Token counts reported by the provider. None means the provider did not report it."""

    input_tokens: int | None = None
    output_tokens: int | None = None

    def add(self, other: "Usage") -> "Usage":
        def plus(a: int | None, b: int | None) -> int | None:
            return None if a is None and b is None else (a or 0) + (b or 0)

        return Usage(plus(self.input_tokens, other.input_tokens),
                     plus(self.output_tokens, other.output_tokens))


@dataclass
class Generation:
    text: str
    tool_calls: list[ToolCall]
    usage: Usage
    model: str
    provider: str
    finish_reason: str | None
    duration_ms: int


@dataclass
class StreamDelta:
    """One streamed piece: text deltas, then a final item with ``done=True``."""

    text: str = ""
    done: bool = False
    generation: Generation | None = None


@dataclass
class Capabilities:
    streaming: bool = True
    # Whether the provider/model is known to return structured tool calls. "advertised" means
    # the model metadata claims support; callers must still handle a reply without a tool call.
    native_tools: Literal["no", "advertised", "yes"] = "no"
    json_output: bool = False
    usage: bool = False


@dataclass
class ProviderStatus:
    provider: str
    model: str | None
    available: bool
    message: str
    capabilities: Capabilities = field(default_factory=Capabilities)
    context_length: int | None = None
    installed_models: list[str] = field(default_factory=list)
    sends_code_off_machine: bool = False


ErrorCode = Literal[
    "not_configured", "unavailable", "model_missing", "timeout", "cancelled",
    "invalid_response", "provider_error",
]

_STATUS_FOR: dict[str, int] = {
    "not_configured": 503, "unavailable": 503, "model_missing": 503, "timeout": 504,
    "cancelled": 499, "invalid_response": 502, "provider_error": 502,
}


class LLMError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def status_code(self) -> int:
        return _STATUS_FOR.get(self.code, 502)


class CancelToken:
    """Cooperative cancellation shared between a request and the work it started.

    Work blocked in I/O (e.g. waiting for a model's first token) registers a callback that
    interrupts it, such as closing the HTTP response, so cancellation takes effect at once."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[], None]] = []

    def cancel(self) -> None:
        with self._lock:
            self._event.set()
            callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            try:
                callback()
            except Exception:  # an interrupt hook must never break cancellation
                pass

    def on_cancel(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Run ``callback`` on cancellation (immediately if already cancelled). Returns a
        function that unregisters it."""
        with self._lock:
            if not self._event.is_set():
                self._callbacks.append(callback)
                return lambda: self._remove(callback)
        callback()
        return lambda: None

    def _remove(self, callback: Callable[[], None]) -> None:
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise LLMError("cancelled", "Generation was cancelled.")


class Deadline:
    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self._end = time.monotonic() + seconds

    @property
    def remaining(self) -> float:
        return self._end - time.monotonic()

    def check(self, what: str = "The model request") -> None:
        if self.remaining <= 0:
            raise LLMError("timeout", f"{what} exceeded {self.seconds:g} s.")


class LLMProvider(Protocol):
    name: str
    model: str

    def status(self) -> ProviderStatus: ...

    def capabilities(self) -> Capabilities: ...

    def stream(
        self,
        messages: list[Message],
        *,
        system: str,
        tools: list[ToolSpec] | None = None,
        max_tokens: int,
        temperature: float,
        cancel: CancelToken | None = None,
    ) -> Iterator[StreamDelta]: ...

    def close(self) -> None: ...


def generate(
    provider: LLMProvider,
    messages: list[Message],
    *,
    system: str,
    tools: list[ToolSpec] | None = None,
    max_tokens: int,
    temperature: float,
    cancel: CancelToken | None = None,
) -> Generation:
    """Non-streaming generation, built on ``stream`` so timeouts and cancellation are uniform."""
    for delta in provider.stream(messages, system=system, tools=tools, max_tokens=max_tokens,
                                 temperature=temperature, cancel=cancel):
        if delta.done and delta.generation is not None:
            return delta.generation
    raise LLMError("invalid_response", "The provider ended the response without completing it.")
