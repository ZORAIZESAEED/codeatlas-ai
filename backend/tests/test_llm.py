import json

import httpx
import pytest

from app.core.config import Settings
from app.services.llm.base import CancelToken, LLMError, Message, ToolSpec, Usage, generate
from app.services.llm.fake import FakeProvider
from app.services.llm.ollama import OllamaProvider
from app.services.llm.registry import create_provider, provider_status

SHOW = {"capabilities": ["completion", "tools"], "model_info": {"qwen2.context_length": 4096}}


def ndjson(*items) -> bytes:
    return b"".join(json.dumps(i).encode() + b"\n" for i in items)


def ollama(handler, **kwargs) -> OllamaProvider:
    return OllamaProvider("http://ollama.test", "m:1", context_tokens=kwargs.pop("ctx", 8192),
                          timeout_seconds=kwargs.pop("timeout", 30.0),
                          transport=httpx.MockTransport(handler))


def router(chat=None, show=SHOW, tags=None, show_status=200):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            return httpx.Response(show_status, json=show)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=tags or {"models": [{"name": "m:1"}]})
        if request.url.path == "/api/chat":
            seen["body"] = json.loads(request.content)
            return chat(request) if callable(chat) else chat
        return httpx.Response(404)

    return handler, seen


def chat_ok(text_parts=("Hello ", "world"), done=None):
    items = [{"message": {"role": "assistant", "content": p}, "done": False} for p in text_parts]
    items.append(done or {"message": {"role": "assistant", "content": ""}, "done": True,
                          "done_reason": "stop", "prompt_eval_count": 12, "eval_count": 3})
    return httpx.Response(200, content=ndjson(*items))


def test_registry_requires_explicit_model_and_known_provider():
    with pytest.raises(LLMError) as exc:
        create_provider(Settings(llm_provider="ollama", llm_model=None))
    assert exc.value.code == "not_configured" and "LLM_MODEL" in exc.value.message
    with pytest.raises(LLMError) as exc:
        create_provider(Settings(llm_provider="anthropic", llm_model="x"))
    assert "not enabled" in exc.value.message
    with pytest.raises(LLMError):
        create_provider(Settings(llm_provider="mystery", llm_model="x"))
    assert isinstance(create_provider(Settings(llm_provider="fake")), FakeProvider)


def test_status_never_raises_and_explains_how_to_start_ollama():
    # Nothing listens on port 9: a refused connection becomes an unavailable status.
    settings = Settings(llm_provider="ollama", llm_model="m:1",
                        ollama_base_url="http://127.0.0.1:9", llm_request_timeout_seconds=2)
    status = provider_status(settings)
    assert not status.available
    assert "ollama serve" in status.message


def test_status_reports_model_capabilities_and_missing_models():
    handler, _ = router()
    status = ollama(handler).status()
    assert status.available and status.context_length == 4096
    assert status.capabilities.native_tools == "advertised"
    assert status.installed_models == ["m:1"]

    handler, _ = router(show={"error": "not found"}, show_status=404)
    status = ollama(handler).status()
    assert not status.available and "ollama pull m:1" in status.message


def test_stream_yields_deltas_usage_and_clamps_context():
    handler, seen = router(chat=chat_ok())
    provider = ollama(handler, ctx=8192)
    deltas = list(provider.stream([Message("user", "hi")], system="sys", max_tokens=50,
                                  temperature=0.1))
    assert [d.text for d in deltas if not d.done] == ["Hello ", "world"]
    final = deltas[-1].generation
    assert final.text == "Hello world" and final.usage == Usage(12, 3)
    assert final.finish_reason == "stop"
    # num_ctx is always sent and never exceeds the model's own context length.
    assert seen["body"]["options"] == {"temperature": 0.1, "num_predict": 50, "num_ctx": 4096}
    assert seen["body"]["messages"][0] == {"role": "system", "content": "sys"}


def test_native_tool_calls_are_parsed_and_malformed_ones_rejected():
    call = {"message": {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "find_symbol", "arguments": {"name": "login"}}}]}, "done": False}
    handler, seen = router(
        chat=httpx.Response(200, content=ndjson(call, {"done": True, "message": {}}))
    )
    tool = ToolSpec("find_symbol", "Find a symbol", {"type": "object", "properties": {}})
    result = generate(ollama(handler), [Message("user", "q")], system="", tools=[tool],
                      max_tokens=10, temperature=0)
    assert result.tool_calls[0].name == "find_symbol"
    assert result.tool_calls[0].arguments == {"name": "login"}
    assert seen["body"]["tools"][0]["function"]["name"] == "find_symbol"

    bad = {"message": {"tool_calls": [{"function": {"arguments": "{}"}}]}, "done": False}
    handler, _ = router(chat=httpx.Response(200, content=ndjson(bad)))
    with pytest.raises(LLMError) as exc:
        generate(ollama(handler), [Message("user", "q")], system="", max_tokens=10,
                 temperature=0)
    assert exc.value.code == "invalid_response"


@pytest.mark.parametrize(
    "response, code",
    [
        (httpx.Response(200, content=b"not json\n"), "invalid_response"),
        (httpx.Response(200, content=ndjson({"message": {"content": "partial"}, "done": False})),
         "invalid_response"),  # stream ended without done
        (httpx.Response(404, json={"error": "model not found"}), "model_missing"),
        (httpx.Response(500, json={"error": "boom"}), "provider_error"),
        (httpx.Response(200, content=ndjson({"error": "out of memory"})), "provider_error"),
    ],
)
def test_invalid_and_failed_responses_raise_typed_errors(response, code):
    handler, _ = router(chat=response)
    with pytest.raises(LLMError) as exc:
        generate(ollama(handler), [Message("user", "q")], system="", max_tokens=10,
                 temperature=0)
    assert exc.value.code == code


def test_timeouts_and_connection_errors():
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    handler, _ = router(chat=slow)
    with pytest.raises(LLMError) as exc:
        generate(ollama(handler), [Message("user", "q")], system="", max_tokens=5, temperature=0)
    assert exc.value.code == "timeout"

    # The wall-clock deadline also applies while the stream is still producing data.
    handler, _ = router(chat=chat_ok())
    with pytest.raises(LLMError) as exc:
        generate(ollama(handler, timeout=0), [Message("user", "q")], system="", max_tokens=5,
                 temperature=0)
    assert exc.value.code == "timeout"

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    handler, _ = router(chat=refused)
    with pytest.raises(LLMError) as exc:
        generate(ollama(handler), [Message("user", "q")], system="", max_tokens=5, temperature=0)
    assert exc.value.code == "unavailable"


def test_cancellation_stops_the_stream():
    handler, _ = router(chat=chat_ok(text_parts=("a ", "b ", "c ")))
    cancel = CancelToken()
    received = []
    with pytest.raises(LLMError) as exc:
        for delta in ollama(handler).stream([Message("user", "q")], system="", max_tokens=5,
                                            temperature=0, cancel=cancel):
            received.append(delta.text)
            cancel.cancel()
    assert exc.value.code == "cancelled" and received == ["a "]


def test_fake_provider_cites_evidence_and_reports_no_usage():
    fake = FakeProvider()
    prompt = "[E1] app/a.py lines 1-4\n...\n[E2] app/b.py lines 2-9\n..."
    result = generate(fake, [Message("user", prompt)], system="", max_tokens=5, temperature=0)
    assert "[E1]" in result.text and "[E2]" in result.text
    assert result.usage == Usage(None, None)
    assert Usage(1, None).add(Usage(2, 3)) == Usage(3, 3)
    assert Usage().add(Usage()) == Usage(None, None)


def test_llm_status_endpoint(api):
    response = api.get("/api/llm/status")
    assert response.status_code == 200
    body = response.json()
    # Test settings configure no chat model: a clear, non-crashing status.
    assert body["available"] is False and "LLM_MODEL" in body["message"]

    api.app.state.llm_factory = lambda s: FakeProvider(model="fake-chat")
    body = api.get("/api/llm/status").json()
    assert body["available"] is True and body["model"] == "fake-chat"
    assert body["capabilities"]["native_tools"] == "no"


class BlockingStream(httpx.SyncByteStream):
    """A response body that sends nothing until it is closed (like a model still reading
    a long prompt)."""

    def __init__(self) -> None:
        import threading
        self.closed = threading.Event()

    def __iter__(self):
        self.closed.wait(timeout=10)
        return iter(())

    def close(self) -> None:
        self.closed.set()


def test_cancel_interrupts_a_request_waiting_for_the_first_token():
    import threading
    import time

    body = BlockingStream()
    handler, _ = router(chat=httpx.Response(200, stream=body))

    class ClosingTransport(httpx.MockTransport):
        def close(self) -> None:  # a real transport closes its sockets here
            body.close()

    provider = OllamaProvider("http://ollama.test", "m:1", context_tokens=8192,
                              timeout_seconds=30, transport=ClosingTransport(handler))
    cancel = CancelToken()
    threading.Timer(0.2, cancel.cancel).start()
    started = time.perf_counter()
    with pytest.raises(LLMError) as exc:
        generate(provider, [Message("user", "q")], system="", max_tokens=5, temperature=0,
                 cancel=cancel)
    assert exc.value.code == "cancelled"
    assert time.perf_counter() - started < 5 and body.closed.is_set()


def test_cancel_token_callbacks():
    token = CancelToken()
    calls = []
    unregister = token.on_cancel(lambda: calls.append("a"))
    token.on_cancel(lambda: 1 / 0)  # a failing hook does not stop the others
    token.on_cancel(lambda: calls.append("b"))
    unregister()
    token.cancel()
    assert calls == ["b"] and token.cancelled
    token.on_cancel(lambda: calls.append("late"))  # already cancelled: runs at once
    assert calls == ["b", "late"]
