import pytest

from app.services.llm.base import LLMError
from app.services.llm.fake import FakeProvider
from tests.chat_helpers import ask, stream_events, use_fake
from tests.search_helpers import db_rows, import_repo, index, workspace_file, write_lf


@pytest.fixture
def project(api):
    project = import_repo(api)
    index(api, project["id"])
    return project


def conversations(api, project_id):
    response = api.get(f"/api/projects/{project_id}/conversations")
    assert response.status_code == 200
    return response.json()


def test_conversation_crud_and_project_isolation(api, settings, project):
    pid = project["id"]
    created = api.post(f"/api/projects/{pid}/conversations", json={"title": "  Auth\x00 flow "})
    assert created.status_code == 201
    conv = created.json()
    assert conv["title"] == "Auth flow" and conv["message_count"] == 0
    assert [c["id"] for c in conversations(api, pid)] == [conv["id"]]

    renamed = api.patch(f"/api/projects/{pid}/conversations/{conv['id']}", json={"title": "Login"})
    assert renamed.status_code == 200 and renamed.json()["title"] == "Login"

    # Another project cannot see, rename, delete or chat in this conversation.
    other = import_repo(api, name="other")
    index(api, other["id"])
    use_fake(api)
    base = f"/api/projects/{other['id']}/conversations/{conv['id']}"
    assert api.get(base).status_code == 404
    assert api.patch(base, json={"title": "x"}).status_code == 404
    assert api.delete(base).status_code == 404
    response = api.post(f"/api/projects/{other['id']}/chat",
                        json={"question": "auth?", "conversation_id": conv["id"]})
    assert response.status_code == 404 and response.json()["code"] == "not_found"
    assert conversations(api, other["id"]) == []

    assert api.get("/api/projects/nope/conversations").status_code == 404
    assert api.delete(f"/api/projects/{pid}/conversations/{conv['id']}").status_code == 204
    assert conversations(api, pid) == []


def test_chat_persists_question_answer_and_verified_citations(api, settings, project):
    pid = project["id"]
    slot = use_fake(api)
    body = ask(api, pid, "How are users authenticated?", mode="baseline")
    message = body["message"]
    assert message["status"] == "completed" and message["role"] == "assistant"
    assert message["provider"] == "fake" and message["model"] == "fake-chat"
    assert message["citations"], message
    for c in message["citations"]:
        assert c["reference"] == f"{c['file_path']}:{c['start_line']}-{c['end_line']}"
        assert f"[{c['evidence_id']}]" in message["content"]
    assert body["conversation"]["title"] == "How are users authenticated?"
    assert body["conversation"]["message_count"] == 2
    assert message["run"]["mode"] == "baseline" and message["run"]["llm_request_count"] == 1

    detail = api.get(f"/api/projects/{pid}/conversations/{body['conversation']['id']}").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["citations"] == message["citations"]
    assert detail["stale"] is False
    # Citation rows point at real file records of this project.
    rows = db_rows(settings, "SELECT c.file_path, f.path, f.project_id FROM message_citations c "
                             "JOIN project_files f ON f.id = c.file_id")
    assert rows and all(r[0] == r[1] and r[2] == pid for r in rows)
    assert slot.created == 1


def test_follow_up_uses_bounded_history_and_fresh_retrieval(api, settings, project):
    pid = project["id"]
    slot = use_fake(api)
    first = ask(api, pid, "How does authentication work?", mode="baseline")
    conv_id = first["conversation"]["id"]
    ask(api, pid, "Where is the token created?", mode="baseline", conversation_id=conv_id)
    prompt = slot.provider.requests[-1].messages[0].content
    assert "User asked: How does authentication work?" in prompt
    assert "context only, not evidence" in prompt
    # Fresh evidence was retrieved for the follow-up and fenced again.
    assert "<<<UNTRUSTED_SOURCE E1" in prompt
    detail = api.get(f"/api/projects/{pid}/conversations/{conv_id}").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"] * 2


def test_reanalysis_marks_conversation_context_stale(api, settings, project):
    pid = project["id"]
    use_fake(api)
    first = ask(api, pid, "How does authentication work?", mode="baseline")
    assert api.post(f"/api/projects/{pid}/analyze").status_code == 200
    detail = api.get(f"/api/projects/{pid}/conversations/{first['conversation']['id']}").json()
    assert detail["stale"] is True


def test_chat_errors_are_clear_and_store_nothing_on_precheck(api, settings, project):
    pid = project["id"]
    # No chat model configured in test settings: a 503 explaining LLM_MODEL.
    response = api.post(f"/api/projects/{pid}/chat", json={"question": "auth?"})
    assert response.status_code == 503 and "LLM_MODEL" in response.json()["detail"]

    use_fake(api, FakeProvider(available=False))
    response = api.post(f"/api/projects/{pid}/chat", json={"question": "auth?"})
    assert response.status_code == 503 and response.json()["code"] == "llm_unavailable"

    use_fake(api)
    unindexed = import_repo(api, name="raw")
    response = api.post(f"/api/projects/{unindexed['id']}/chat", json={"question": "auth?"})
    assert response.status_code == 409 and response.json()["code"] == "index_missing"
    assert api.post("/api/projects/missing/chat", json={"question": "x"}).status_code == 404
    assert api.post(f"/api/projects/{pid}/chat", json={"question": ""}).status_code == 422
    assert api.post(f"/api/projects/{pid}/chat",
                    json={"question": "x", "mode": "agent"}).status_code == 422
    response = api.post(f"/api/projects/{pid}/chat",
                        json={"question": "x", "file_paths": ["../../etc/passwd"]})
    assert response.status_code == 422 and response.json()["code"] == "invalid_file"
    # Prechecks fail before a turn is stored.
    assert db_rows(settings, "SELECT count(*) FROM chat_messages")[0][0] == 0


def test_failed_generation_is_stored_as_failed_without_answer(api, settings, project):
    pid = project["id"]
    use_fake(api, FakeProvider(script=[LLMError("timeout", "The Ollama request exceeded 1 s.")]))
    response = api.post(f"/api/projects/{pid}/chat", json={"question": "auth?",
                                                           "mode": "baseline"})
    assert response.status_code == 504 and response.json()["code"] == "llm_timeout"
    rows = db_rows(settings, "SELECT role, status, content, error FROM chat_messages "
                             "ORDER BY role DESC")
    assert rows[0][:2] == ("user", "completed")
    assert rows[1] == ("assistant", "failed", "", "The Ollama request exceeded 1 s.")
    assert db_rows(settings, "SELECT count(*) FROM message_citations")[0][0] == 0
    assert db_rows(settings, "SELECT status FROM investigation_runs")[0][0] == "failed"
    # A failed answer is not used as context for the next question.
    slot = use_fake(api)
    conv_id = conversations(api, pid)[0]["id"]
    ask(api, pid, "Where is the token created?", mode="baseline", conversation_id=conv_id)
    assert "Earlier in this conversation" not in slot.provider.requests[-1].messages[0].content


def test_selected_files_restrict_initial_evidence(api, settings, project):
    slot = use_fake(api)
    body = ask(api, project["id"], "What does this file do?", mode="baseline",
               file_paths=["app/database.py"])
    prompt = slot.provider.requests[-1].messages[0].content
    assert prompt.index("app/database.py") < prompt.index("app/auth.py") \
        if "app/auth.py" in prompt else "app/database.py" in prompt
    assert body["message"]["citations"][0]["file_path"] == "app/database.py"


def test_stale_source_citations_are_rejected_not_rendered(api, settings, project):
    pid = project["id"]

    def edit_then_answer(messages, system, tools):
        # The file changes after the evidence was read but before citations are validated.
        path = workspace_file(settings, pid, "app/auth.py")
        write_lf(path, "# edited\n" + path.read_bytes().decode())  # shifts every line
        cited = [line.split("]")[0][1:] for line in messages[0].content.splitlines()
                 if line.startswith("[E")]
        return "Evidence: " + " ".join(f"[{c}]" for c in cited)

    use_fake(api, FakeProvider(script=[edit_then_answer]))
    message = ask(api, pid, "How are passwords hashed?", mode="baseline")["message"]
    assert all(c["file_path"] != "app/auth.py" for c in message["citations"])
    assert any("stale source" in w for w in message["warnings"])


def test_deleting_conversation_or_project_keeps_or_removes_the_right_data(api, settings,
                                                                          project):
    pid = project["id"]
    use_fake(api)
    conv = ask(api, pid, "auth?", mode="baseline")["conversation"]
    entries = db_rows(settings, "SELECT count(*) FROM index_entries")[0][0]
    files = db_rows(settings, "SELECT count(*) FROM project_files")[0][0]
    assert api.delete(f"/api/projects/{pid}/conversations/{conv['id']}").status_code == 204
    for table in ("conversations", "chat_messages", "message_citations", "investigation_runs"):
        assert db_rows(settings, f"SELECT count(*) FROM {table}")[0][0] == 0
    assert db_rows(settings, "SELECT count(*) FROM index_entries")[0][0] == entries
    assert db_rows(settings, "SELECT count(*) FROM project_files")[0][0] == files
    assert workspace_file(settings, pid, "app/auth.py").exists()

    ask(api, pid, "auth?", mode="baseline")
    assert api.delete(f"/api/projects/{pid}").status_code == 204
    for table in ("conversations", "chat_messages", "message_citations", "investigation_runs"):
        assert db_rows(settings, f"SELECT count(*) FROM {table}")[0][0] == 0


def test_interrupted_answers_are_recovered_at_startup(api, settings, project):
    from fastapi.testclient import TestClient

    from app.main import create_app

    use_fake(api)
    ask(api, project["id"], "auth?", mode="baseline")
    db_path = settings.database_url.removeprefix("sqlite:///")
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE chat_messages SET status='generating' WHERE role='assistant'")
        conn.execute("UPDATE investigation_runs SET status='running'")
    with TestClient(create_app(settings)):
        pass
    assert db_rows(settings, "SELECT status FROM chat_messages WHERE role='assistant'") == \
        [("failed",)]
    assert db_rows(settings, "SELECT status FROM investigation_runs") == [("failed",)]


# -- streaming ---------------------------------------------------------------------------------

def test_stream_event_order_and_completion(api, settings, project):
    use_fake(api)
    events = stream_events(api, project["id"], "How are users authenticated?", mode="baseline")
    names = [e for e, _ in events]
    assert names[0] == "turn_started" and names[-1] == "completed"
    order = ["turn_started", "retrieval_started", "retrieval_completed", "generation_started",
             "answer_delta", "citation_update", "completed"]
    assert [n for n in dict.fromkeys(names)] == order
    streamed = "".join(d["text"] for e, d in events if e == "answer_delta")
    completed = events[-1][1]
    assert completed["message"]["content"] == streamed.strip()
    assert completed["message"]["status"] == "completed"
    assert events[-2][1]["citations"] == [
        {k: c[k] for k in ("evidence_id", "file_path", "start_line", "end_line")}
        for c in completed["message"]["citations"]
    ]
    assert "error" not in names


def test_stream_provider_failure_mid_answer(api, settings, project):
    use_fake(api, FakeProvider(fail_after_chunks=3))
    events = stream_events(api, project["id"], "How are users authenticated?", mode="baseline")
    names = [e for e, _ in events]
    assert names.count("answer_delta") == 3 and names[-1] == "error"
    assert "completed" not in names
    assert events[-1][1]["status"] == "failed"
    # The partial text was streamed but is not stored as an answer.
    assert db_rows(settings, "SELECT status, content FROM chat_messages WHERE role='assistant'") \
        == [("failed", "")]


def test_stream_prechecks_return_http_errors(api, settings, project):
    use_fake(api, FakeProvider(available=False))
    response = api.post(f"/api/projects/{project['id']}/chat/stream", json={"question": "x"})
    assert response.status_code == 503 and response.json()["code"] == "llm_unavailable"


def test_cancellation_stores_cancelled_turn(api, settings, project):
    """Cancellation reaches the running turn (as a client disconnect does)."""
    from app.db.models import Project
    from app.services.chat import TurnInput, execute_turn, prepare_turn
    from app.services.llm.base import CancelToken

    provider = FakeProvider()
    turn = TurnInput(question="How are users authenticated?", mode="baseline")
    cancel = CancelToken()
    seen = []

    def emit(event, data):
        seen.append(event)
        if event == "answer_delta" and seen.count("answer_delta") == 2:
            cancel.cancel()

    with api.app.state.session_factory() as session:
        prepared = prepare_turn(session, settings, provider, project["id"], turn)
        with pytest.raises(LLMError) as exc:
            execute_turn(session, settings, provider, prepared, turn, cancel, emit)
        assert exc.value.code == "cancelled" and session.get(Project, project["id"])
    assert seen.count("answer_delta") == 2 and "citation_update" not in seen
    assert db_rows(settings, "SELECT status, content FROM chat_messages WHERE role='assistant'") \
        == [("cancelled", "")]
    assert db_rows(settings, "SELECT status FROM investigation_runs") == [("cancelled",)]


def test_stream_generator_close_cancels_the_turn(api, settings, project):
    """Closing the SSE response (client gone) must set the turn's cancel token."""
    import asyncio

    from app.api import chat as chat_api

    tokens = []
    original = chat_api.CancelToken

    class Spy(original):
        def __init__(self):
            super().__init__()
            tokens.append(self)

    chat_api.CancelToken = Spy
    try:
        use_fake(api, FakeProvider(chunk_delay=0.05))
        with api.stream("POST", f"/api/projects/{project['id']}/chat/stream",
                        json={"question": "auth?", "mode": "baseline"}) as response:
            for line in response.iter_lines():
                if line.startswith("event: answer_delta"):
                    break  # leave early: the client closes the stream
    finally:
        chat_api.CancelToken = original
    assert tokens and tokens[0].cancelled
    asyncio.run(asyncio.sleep(0.5))  # let the worker observe the cancellation
    status = db_rows(settings, "SELECT status FROM chat_messages WHERE role='assistant'")
    assert status in ([("cancelled",)], [("completed",)])
