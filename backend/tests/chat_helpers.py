import json

from app.services.llm.fake import FakeProvider


class ProviderSlot:
    """Hands the same fake provider to every request so tests can script and inspect it."""

    def __init__(self, provider: FakeProvider | None = None) -> None:
        self.provider = provider or FakeProvider()
        self.created = 0

    def __call__(self, settings):
        self.created += 1
        return self.provider


def use_fake(api, provider: FakeProvider | None = None) -> ProviderSlot:
    slot = ProviderSlot(provider)
    api.app.state.llm_factory = slot
    return slot


def ask(api, project_id: str, question: str, **body) -> dict:
    response = api.post(f"/api/projects/{project_id}/chat", json={"question": question, **body})
    assert response.status_code == 200, response.text
    return response.json()


def stream_events(api, project_id: str, question: str, **body) -> list[tuple[str, dict]]:
    events = []
    with api.stream("POST", f"/api/projects/{project_id}/chat/stream",
                    json={"question": question, **body}) as response:
        assert response.status_code == 200, response.read()
        assert response.headers["content-type"].startswith("text/event-stream")
        event = None
        for line in response.iter_lines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                events.append((event, json.loads(line[6:])))
    return events
