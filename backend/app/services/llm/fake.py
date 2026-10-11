"""Deterministic fake chat provider for tests and offline runs (``LLM_PROVIDER=fake``).

It never contacts a network service. Without a script it writes a short answer that cites every
evidence block it was given (``[E1]`` ...), or says the evidence is missing. Tests can script
replies (text, tool calls, exceptions or callables) and inspect the requests it received.
"""
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from app.services.llm.base import (
    CancelToken,
    Capabilities,
    Generation,
    LLMError,
    Message,
    ProviderStatus,
    StreamDelta,
    ToolCall,
    ToolSpec,
    Usage,
)

EVIDENCE_HEADER = re.compile(r"^\[(E\d+)\] (\S+) lines (\d+)-(\d+)", re.MULTILINE)

Reply = str | list[ToolCall] | Exception | Callable[..., "str | list[ToolCall]"]


@dataclass
class FakeRequest:
    system: str
    messages: list[Message]
    tools: list[ToolSpec] | None


@dataclass
class FakeProvider:
    model: str = "fake-chat"
    script: list[Reply] = field(default_factory=list)
    native_tools: bool = False
    available: bool = True
    chunk_delay: float = 0.0
    fail_after_chunks: int | None = None
    requests: list[FakeRequest] = field(default_factory=list)
    name: str = "fake"

    def close(self) -> None:
        pass

    def capabilities(self) -> Capabilities:
        return Capabilities(streaming=True, native_tools="yes" if self.native_tools else "no",
                            json_output=True, usage=False)

    def status(self) -> ProviderStatus:
        if not self.available:
            raise LLMError("unavailable", "The fake provider is configured as unavailable.")
        return ProviderStatus(provider=self.name, model=self.model, available=True,
                              message="Fake provider (tests only).",
                              capabilities=self.capabilities(), context_length=32768,
                              installed_models=[self.model])

    def _default_reply(self, messages: list[Message]) -> str:
        prompt = messages[-1].content if messages else ""
        found = EVIDENCE_HEADER.findall(prompt)
        if not found:
            return "The indexed evidence does not show how this works."
        parts = [f"`{path}` lines {start}-{end} is relevant [{eid}]."
                 for eid, path, start, end in found]
        return "Based on the repository evidence: " + " ".join(parts)

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
        if not self.available:
            raise LLMError("unavailable", "The fake provider is configured as unavailable.")
        started = time.perf_counter()
        self.requests.append(FakeRequest(system, list(messages), tools))
        reply: Reply = self.script.pop(0) if self.script else self._default_reply(messages)
        if callable(reply) and not isinstance(reply, Exception):
            reply = reply(messages=messages, system=system, tools=tools)
        if isinstance(reply, Exception):
            raise reply
        calls: list[ToolCall] = []
        text = ""
        if isinstance(reply, list):
            calls = reply if self.native_tools and tools else []
            if not calls:
                # A model without native tools can only answer in text.
                text = "I would call: " + ", ".join(c.name for c in reply)
        else:
            text = reply
        words = re.findall(r"\S+\s*", text) or ([text] if text else [])
        for i, word in enumerate(words):
            if cancel is not None:
                cancel.raise_if_cancelled()
            if self.fail_after_chunks is not None and i >= self.fail_after_chunks:
                raise LLMError("provider_error", "Fake provider failure mid-stream.")
            if self.chunk_delay:
                time.sleep(self.chunk_delay)
            yield StreamDelta(text=word)
        if cancel is not None:
            cancel.raise_if_cancelled()
        yield StreamDelta(done=True, generation=Generation(
            text=text, tool_calls=calls,
            usage=Usage(),  # the fake has no tokenizer, so it reports no usage
            model=self.model, provider=self.name,
            finish_reason="tool_calls" if calls else "stop",
            duration_ms=int((time.perf_counter() - started) * 1000),
        ))
