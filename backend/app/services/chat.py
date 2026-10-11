"""One chat turn: validate -> persist question -> answer (baseline or investigation) -> persist.

Shared by the JSON endpoint and the SSE endpoint. Checks that can fail fast (unknown project or
conversation, missing index, unavailable model) run in ``prepare_turn`` before anything is
stored or streamed. ``execute_turn`` emits only events for work that actually ran.
"""
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import ChatMessage, Conversation, Project, ProjectFile, SearchIndex
from app.services import conversations as store
from app.services.investigation.controller import run_investigation
from app.services.llm.base import CancelToken, LLMError, LLMProvider
from app.services.rag.answer import AnswerRequest, ChatError, Emit, run_baseline
from app.services.retrieval.hybrid import MODES as RETRIEVAL_MODES

MODES = ("baseline", "investigation")
MAX_SELECTED_FILES = 5


@dataclass
class TurnInput:
    question: str
    conversation_id: str | None = None
    mode: str = "investigation"
    retrieval_mode: str | None = None
    file_paths: list[str] = field(default_factory=list)


@dataclass
class PreparedTurn:
    project_id: str
    conversation_id: str
    question: str
    created_conversation: bool


def _clean_question(raw: str, limit: int) -> str:
    text = "".join(ch for ch in raw if ch.isprintable() or ch in "\n\t").strip()
    if not text:
        raise ChatError("invalid_question", "The question is empty.", 422)
    if len(text) > limit:
        raise ChatError("invalid_question", f"The question is longer than {limit} characters.",
                        422)
    return text


def prepare_turn(
    session: Session, settings: Settings, provider: LLMProvider, project_id: str,
    turn: TurnInput,
) -> PreparedTurn:
    project = session.get(Project, project_id)
    if project is None:
        raise ChatError("not_found", "Project not found.", 404)
    question = _clean_question(turn.question, settings.chat_question_max_chars)
    if turn.mode not in MODES:
        raise ChatError("invalid_mode", f"Unknown chat mode '{turn.mode}'.", 422)
    if turn.retrieval_mode is not None and turn.retrieval_mode not in RETRIEVAL_MODES:
        raise ChatError("invalid_mode", f"Unknown retrieval mode '{turn.retrieval_mode}'.", 422)
    if len(turn.file_paths) > MAX_SELECTED_FILES:
        raise ChatError("invalid_file", f"Select at most {MAX_SELECTED_FILES} files.", 422)
    for path in turn.file_paths:  # manifest paths of this project only
        if session.scalar(select(ProjectFile.id).where(
                ProjectFile.project_id == project.id, ProjectFile.path == path)) is None:
            raise ChatError("invalid_file", f"'{path[:200]}' is not a file of this project.", 422)
    state = session.get(SearchIndex, project.id)
    if state is None or state.indexed_at is None:
        raise ChatError("index_missing",
                        "This project has no search index yet. Build the index before chatting.",
                        409)
    created = False
    if turn.conversation_id:
        try:
            conversation = store.get_conversation(session, project.id, turn.conversation_id)
        except store.ConversationNotFound as exc:
            raise ChatError("not_found", "Conversation not found.", 404) from exc
    else:
        conversation = None
    status = provider.status()  # raises LLMError when Ollama or the model is unavailable
    if not status.available:
        raise LLMError("unavailable", status.message)
    if conversation is None:
        conversation = store.create_conversation(session, project)
        created = True
    return PreparedTurn(project.id, conversation.id, question, created)


def execute_turn(
    session: Session,
    settings: Settings,
    provider: LLMProvider,
    prepared: PreparedTurn,
    turn: TurnInput,
    cancel: CancelToken,
    emit: Emit,
) -> ChatMessage:
    """Run the turn and persist the outcome. Returns the completed assistant message; on
    failure the turn is stored as failed/cancelled and the error is re-raised."""
    started = time.perf_counter()
    project = session.get(Project, prepared.project_id)
    conversation = session.get(Conversation, prepared.conversation_id)
    history = store.history_turns(session, conversation.id, settings.chat_history_turns)
    history_notes = []
    if store.history_is_stale(session, conversation.id, project):
        history_notes.append("The project was re-analyzed after earlier answers in this "
                             "conversation; treat them as possibly outdated.")
    user, assistant, run = store.start_turn(session, conversation, prepared.question, turn.mode)
    emit("turn_started", {"conversation_id": conversation.id, "conversation_title":
                          conversation.title, "user_message_id": user.id,
                          "assistant_message_id": assistant.id, "mode": turn.mode,
                          "created_conversation": prepared.created_conversation})
    trace: list[dict[str, Any]] = []
    req = AnswerRequest(
        session=session, settings=settings, provider=provider, project=project,
        question=prepared.question, history=history, retrieval_mode=turn.retrieval_mode,
        file_paths=list(turn.file_paths), history_notes=history_notes, cancel=cancel, emit=emit,
    )
    planner = None
    try:
        if turn.mode == "baseline":
            result = run_baseline(req)
        else:
            result, planner = run_investigation(req, trace)
        if history_notes:
            result.warnings.extend(history_notes)
        cancel.raise_if_cancelled()
        duration = int((time.perf_counter() - started) * 1000)
        store.finish_turn(session, project, assistant, run, result, planner, duration)
    except LLMError as exc:
        status = store.CANCELLED if exc.code == "cancelled" else store.FAILED
        store.fail_turn(session, assistant.id, run.id, status, exc.message, trace,
                        int((time.perf_counter() - started) * 1000))
        raise
    except ChatError as exc:
        store.fail_turn(session, assistant.id, run.id, store.FAILED, exc.message, trace,
                        int((time.perf_counter() - started) * 1000))
        raise
    except Exception:
        store.fail_turn(session, assistant.id, run.id, store.FAILED,
                        "Internal error while answering.", trace,
                        int((time.perf_counter() - started) * 1000))
        raise
    emit("citation_update", {"citations": [
        {"evidence_id": e.id, "file_path": e.path, "start_line": e.start_line,
         "end_line": e.end_line} for e in result.citations
    ], "invalid": [{"evidence_id": i.evidence_id, "reason": i.reason}
                   for i in result.invalid_citations]})
    return assistant
