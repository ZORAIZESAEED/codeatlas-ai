"""Conversations and source-grounded chat (Milestone 4).

Every route is scoped by project: a conversation of another project is "not found".
``POST /chat`` returns the finished answer as JSON; ``POST /chat/stream`` sends Server-Sent
Events. Event types (``event:`` field, JSON ``data:``):

  turn_started, investigation_started, retrieval_started, retrieval_completed, planning,
  tool_started, tool_completed, investigation_completed, generation_started, answer_delta,
  citation_update, completed, error

``completed`` carries the stored message (answer text after citation validation) and is sent
only after the answer and its citations were committed. ``error`` ends a stream that failed or
was cancelled; nothing is stored as an answer in that case.
"""
import asyncio
import json
import logging
import queue
import threading
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.api.llm import ProviderFactory, get_provider_factory
from app.api.projects import _get_project, get_settings_dep
from app.core.config import Settings
from app.db.models import ChatMessage, Conversation
from app.db.session import get_db
from app.schemas import (
    ChatMessageOut,
    ChatRequest,
    ChatResponse,
    ConversationCreate,
    ConversationDetail,
    ConversationOut,
    ConversationRename,
    InvestigationRunOut,
    MessageCitationOut,
)
from app.services import conversations as store
from app.services.chat import TurnInput, execute_turn, prepare_turn
from app.services.llm.base import CancelToken, LLMError
from app.services.rag.answer import ChatError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["chat"])

DISCONNECT_POLL_SECONDS = 0.5


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": message, "code": code})


def _llm_error(exc: LLMError) -> JSONResponse:
    return _error(exc.status_code, f"llm_{exc.code}", exc.message)


def conversation_out(session: Session, conversation: Conversation,
                     message_count: int | None = None) -> ConversationOut:
    if message_count is None:
        message_count = len(store.messages_of(session, conversation.id))
    return ConversationOut(id=conversation.id, project_id=conversation.project_id,
                           title=conversation.title, created_at=conversation.created_at,
                           updated_at=conversation.updated_at, message_count=message_count)


def messages_out(session: Session, messages: list[ChatMessage]) -> list[ChatMessageOut]:
    ids = [m.id for m in messages if m.role == "assistant"]
    citations = store.citations_of(session, ids)
    runs = store.runs_of(session, ids)
    result = []
    for m in messages:
        run = runs.get(m.id)
        result.append(ChatMessageOut(
            id=m.id, role=m.role, content=m.content, status=m.status, mode=m.mode,
            provider=m.provider, model=m.model_identifier, error=m.error,
            warnings=json.loads(m.warnings) if m.warnings else [],
            input_tokens=m.input_tokens, output_tokens=m.output_tokens,
            analysis_revision=m.analysis_revision, created_at=m.created_at,
            completed_at=m.completed_at,
            citations=[MessageCitationOut(
                evidence_id=c.evidence_id, file_id=c.file_id, chunk_id=c.chunk_id,
                file_path=c.file_path, start_line=c.start_line, end_line=c.end_line,
                symbol=c.symbol, source_revision=c.source_revision,
                reference=f"{c.file_path}:{c.start_line}-{c.end_line}",
            ) for c in citations.get(m.id, [])],
            run=InvestigationRunOut(
                mode=run.mode, planner=run.planner, status=run.status,
                tool_call_count=run.tool_call_count, llm_request_count=run.llm_request_count,
                evidence_count=run.evidence_count, duration_ms=run.duration_ms,
                trace=json.loads(run.trace) if run.trace else [],
            ) if run is not None else None,
        ))
    return result


def _conversation_or_404(db: Session, project_id: str, conversation_id: str):
    try:
        return store.get_conversation(db, project_id, conversation_id)
    except store.ConversationNotFound:
        return None


@router.post("/{project_id}/conversations", response_model=ConversationOut, status_code=201)
def create_conversation(project_id: str, payload: ConversationCreate,
                        db: Session = Depends(get_db)) -> ConversationOut:
    project = _get_project(db, project_id)
    return conversation_out(db, store.create_conversation(db, project, payload.title), 0)


@router.get("/{project_id}/conversations", response_model=list[ConversationOut])
def list_conversations(project_id: str, db: Session = Depends(get_db)) -> list[ConversationOut]:
    project = _get_project(db, project_id)
    return [conversation_out(db, c, n) for c, n in store.list_conversations(db, project.id)]


@router.get("/{project_id}/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(project_id: str, conversation_id: str, db: Session = Depends(get_db)):
    project = _get_project(db, project_id)
    conversation = _conversation_or_404(db, project.id, conversation_id)
    if conversation is None:
        return _error(404, "not_found", "Conversation not found.")
    messages = store.messages_of(db, conversation.id)
    return ConversationDetail(
        conversation=conversation_out(db, conversation, len(messages)),
        messages=messages_out(db, messages),
        project_analysis_revision=project.analysis_revision,
        stale=store.history_is_stale(db, conversation.id, project),
    )


@router.patch("/{project_id}/conversations/{conversation_id}", response_model=ConversationOut)
def rename_conversation(project_id: str, conversation_id: str, payload: ConversationRename,
                        db: Session = Depends(get_db)):
    project = _get_project(db, project_id)
    conversation = _conversation_or_404(db, project.id, conversation_id)
    if conversation is None:
        return _error(404, "not_found", "Conversation not found.")
    return conversation_out(db, store.rename_conversation(db, conversation, payload.title))


@router.delete("/{project_id}/conversations/{conversation_id}", status_code=204)
def delete_conversation(project_id: str, conversation_id: str, db: Session = Depends(get_db)):
    project = _get_project(db, project_id)
    conversation = _conversation_or_404(db, project.id, conversation_id)
    if conversation is None:
        return _error(404, "not_found", "Conversation not found.")
    store.delete_conversation(db, conversation)
    return Response(status_code=204)


def _turn(payload: ChatRequest) -> TurnInput:
    return TurnInput(question=payload.question, conversation_id=payload.conversation_id,
                     mode=payload.mode, retrieval_mode=payload.retrieval_mode,
                     file_paths=list(payload.file_paths))


def _response(session: Session, conversation_id: str, message: ChatMessage) -> ChatResponse:
    conversation = session.get(Conversation, conversation_id)
    return ChatResponse(conversation=conversation_out(session, conversation),
                        message=messages_out(session, [message])[0])


@router.post("/{project_id}/chat", response_model=ChatResponse)
def chat(
    project_id: str,
    payload: ChatRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    factory: ProviderFactory = Depends(get_provider_factory),
):
    """Answer one question and return the stored, citation-validated message."""
    try:
        provider = factory(settings)
    except LLMError as exc:
        return _llm_error(exc)
    try:
        turn = _turn(payload)
        prepared = prepare_turn(db, settings, provider, project_id, turn)
        message = execute_turn(db, settings, provider, prepared, turn, CancelToken(),
                               lambda event, data: None)
        return _response(db, prepared.conversation_id, message)
    except ChatError as exc:
        return _error(exc.status_code, exc.code, exc.message)
    except LLMError as exc:
        return _llm_error(exc)
    finally:
        provider.close()


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.post("/{project_id}/chat/stream")
async def chat_stream(
    project_id: str,
    payload: ChatRequest,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    factory: ProviderFactory = Depends(get_provider_factory),
):
    session_factory = request.app.state.session_factory
    turn = _turn(payload)
    try:
        provider = factory(settings)
    except LLMError as exc:
        return _llm_error(exc)

    def prepare():
        with session_factory() as session:
            return prepare_turn(session, settings, provider, project_id, turn)

    # Fail fast with a normal HTTP error before any event is streamed.
    try:
        prepared = await run_in_threadpool(prepare)
    except ChatError as exc:
        provider.close()
        return _error(exc.status_code, exc.code, exc.message)
    except LLMError as exc:
        provider.close()
        return _llm_error(exc)

    cancel = CancelToken()
    events: queue.Queue = queue.Queue()

    def worker() -> None:
        with session_factory() as session:
            try:
                message = execute_turn(session, settings, provider, prepared, turn, cancel,
                                       lambda event, data: events.put((event, data)))
                body = _response(session, prepared.conversation_id, message)
                events.put(("completed", body.model_dump(mode="json")))
            except LLMError as exc:
                events.put(("error", {"code": f"llm_{exc.code}", "message": exc.message,
                                      "status": "cancelled" if exc.code == "cancelled"
                                      else "failed"}))
            except ChatError as exc:
                events.put(("error", {"code": exc.code, "message": exc.message,
                                      "status": "failed"}))
            except Exception:
                logger.exception("Chat turn failed")
                events.put(("error", {"code": "internal", "status": "failed",
                                      "message": "Internal error while answering."}))
            finally:
                provider.close()
                events.put(None)

    threading.Thread(target=worker, name="chat-turn", daemon=True).start()

    async def stream():
        loop = asyncio.get_running_loop()
        last_check = loop.time()
        try:
            while True:
                try:
                    item = events.get_nowait()
                except queue.Empty:
                    if loop.time() - last_check >= DISCONNECT_POLL_SECONDS:
                        last_check = loop.time()
                        if await request.is_disconnected():
                            cancel.cancel()
                            return
                    await asyncio.sleep(0.03)
                    continue
                if item is None:
                    return
                yield _sse(*item)
        finally:
            # Client disconnected or the response was closed: stop the work in progress.
            cancel.cancel()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
