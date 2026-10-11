"""Conversation persistence (Milestone 4).

Every query is scoped by project: a conversation ID from another project is "not found". An
assistant message is written as ``generating`` when a turn starts and becomes ``completed`` only
together with its validated citations, in one transaction. Failed or cancelled turns keep no
answer text, so an incomplete answer is never stored or shown as final.
"""
import json
from datetime import datetime, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.db.models import (
    ChatMessage,
    Conversation,
    InvestigationRun,
    MessageCitation,
    Project,
)
from app.services.rag.answer import AnswerResult, HistoryTurn

GENERATING, COMPLETED, FAILED, CANCELLED = "generating", "completed", "failed", "cancelled"
DEFAULT_TITLE = "New conversation"


class ConversationNotFound(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def clean_title(raw: str | None, fallback: str = DEFAULT_TITLE) -> str:
    title = " ".join("".join(ch for ch in (raw or "") if ch.isprintable()).split())
    return (title or fallback)[:200]


def create_conversation(
    session: Session, project: Project, title: str | None = None
) -> Conversation:
    conversation = Conversation(project_id=project.id, title=clean_title(title))
    session.add(conversation)
    session.commit()
    return conversation


def get_conversation(session: Session, project_id: str, conversation_id: str) -> Conversation:
    conversation = session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.project_id == project_id
        )
    )
    if conversation is None:
        raise ConversationNotFound(conversation_id)
    return conversation


def list_conversations(session: Session, project_id: str) -> list[tuple[Conversation, int]]:
    counts = (
        select(ChatMessage.conversation_id, func.count().label("n"))
        .group_by(ChatMessage.conversation_id)
        .subquery()
    )
    rows = session.execute(
        select(Conversation, func.coalesce(counts.c.n, 0))
        .outerjoin(counts, counts.c.conversation_id == Conversation.id)
        .where(Conversation.project_id == project_id)
        .order_by(Conversation.updated_at.desc(), Conversation.created_at.desc())
    ).all()
    return [(c, n) for c, n in rows]


def messages_of(session: Session, conversation_id: str) -> list[ChatMessage]:
    return list(session.scalars(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at, ChatMessage.role.desc())  # user before assistant
    ).all())


def citations_of(session: Session, message_ids: list[str]) -> dict[str, list[MessageCitation]]:
    result: dict[str, list[MessageCitation]] = {m: [] for m in message_ids}
    if message_ids:
        for c in session.scalars(
            select(MessageCitation)
            .where(MessageCitation.message_id.in_(message_ids))
            .order_by(MessageCitation.message_id, MessageCitation.ordinal)
        ).all():
            result[c.message_id].append(c)
    return result


def runs_of(session: Session, message_ids: list[str]) -> dict[str, InvestigationRun]:
    if not message_ids:
        return {}
    runs = session.scalars(
        select(InvestigationRun).where(InvestigationRun.assistant_message_id.in_(message_ids))
    ).all()
    return {r.assistant_message_id: r for r in runs}


def rename_conversation(session: Session, conversation: Conversation, title: str) -> Conversation:
    conversation.title = clean_title(title, conversation.title)
    conversation.updated_at = _now()
    session.commit()
    return conversation


def _delete_rows(session: Session, conversation_ids) -> None:
    message_ids = select(ChatMessage.id).where(ChatMessage.conversation_id.in_(conversation_ids))
    session.execute(delete(MessageCitation).where(MessageCitation.message_id.in_(message_ids)))
    session.execute(
        delete(InvestigationRun).where(InvestigationRun.conversation_id.in_(conversation_ids))
    )
    session.execute(delete(ChatMessage).where(ChatMessage.conversation_id.in_(conversation_ids)))
    session.execute(delete(Conversation).where(Conversation.id.in_(conversation_ids)))


def delete_conversation(session: Session, conversation: Conversation) -> None:
    """Removes chat rows only; repository files, analysis and search indexes are untouched."""
    _delete_rows(session, [conversation.id])
    session.commit()


def delete_project_conversations(session: Session, project_id: str) -> None:
    """Called inside the project delete transaction (SQLite foreign keys are not enforced)."""
    _delete_rows(session, select(Conversation.id).where(Conversation.project_id == project_id))


def history_turns(session: Session, conversation_id: str, limit: int) -> list[HistoryTurn]:
    """Most recent completed question/answer pairs, oldest first."""
    turns: list[HistoryTurn] = []
    question: str | None = None
    for m in messages_of(session, conversation_id):
        if m.role == "user":
            question = m.content
        elif m.role == "assistant" and m.status == COMPLETED and question is not None:
            turns.append(HistoryTurn(question, m.content))
            question = None
    return turns[-limit:] if limit > 0 else []


def history_is_stale(session: Session, conversation_id: str, project: Project) -> bool:
    return session.scalar(
        select(func.count()).select_from(ChatMessage).where(
            ChatMessage.conversation_id == conversation_id,
            ChatMessage.role == "assistant",
            ChatMessage.status == COMPLETED,
            ChatMessage.analysis_revision.is_not(None),
            ChatMessage.analysis_revision != project.analysis_revision,
        )
    ) > 0


def start_turn(
    session: Session, conversation: Conversation, question: str, mode: str
) -> tuple[ChatMessage, ChatMessage, InvestigationRun]:
    now = _now()
    if conversation.title == DEFAULT_TITLE and not session.scalar(
        select(func.count()).select_from(ChatMessage)
        .where(ChatMessage.conversation_id == conversation.id)
    ):
        conversation.title = clean_title(question[:80])
    user = ChatMessage(conversation_id=conversation.id, role="user", content=question,
                       status=COMPLETED, created_at=now, completed_at=now)
    assistant = ChatMessage(conversation_id=conversation.id, role="assistant", content="",
                            status=GENERATING, mode=mode, created_at=now)
    session.add_all([user, assistant])
    session.flush()
    run = InvestigationRun(conversation_id=conversation.id, user_message_id=user.id,
                           assistant_message_id=assistant.id, status="running", mode=mode,
                           created_at=now)
    session.add(run)
    conversation.updated_at = now
    session.commit()
    return user, assistant, run


def finish_turn(
    session: Session,
    project: Project,
    assistant: ChatMessage,
    run: InvestigationRun,
    result: AnswerResult,
    planner: str | None,
    duration_ms: int,
) -> None:
    """Store the validated answer, its citations and the run trace in one transaction."""
    now = _now()
    try:
        assistant.content = result.text
        assistant.status = COMPLETED
        assistant.provider = result.provider
        assistant.model_identifier = result.model
        assistant.warnings = json.dumps(result.warnings) if result.warnings else None
        assistant.input_tokens = result.usage.input_tokens
        assistant.output_tokens = result.usage.output_tokens
        assistant.analysis_revision = project.analysis_revision
        assistant.completed_at = now
        for ordinal, e in enumerate(result.citations):
            session.add(MessageCitation(
                message_id=assistant.id, evidence_id=e.id, file_id=e.file_id,
                chunk_id=e.chunk_id, file_path=e.path, start_line=e.start_line,
                end_line=e.end_line, symbol=e.symbol, source_revision=e.source_revision,
                content_hash=e.content_hash, ordinal=ordinal,
            ))
        run.status = COMPLETED
        run.planner = planner
        run.tool_call_count = result.tool_calls
        run.llm_request_count = result.llm_requests
        run.evidence_count = len(result.evidence)
        run.duration_ms = duration_ms
        checks = ([{"step": "relationship_check", "checks": result.relationships}]
                  if result.relationships else [])
        run.trace = json.dumps(result.trace + checks)
        run.completed_at = now
        session.execute(update(Conversation).where(Conversation.id == assistant.conversation_id)
                        .values(updated_at=now))
        session.commit()
    except BaseException:
        session.rollback()
        raise


def fail_turn(
    session: Session, assistant_id: str, run_id: str, status: str, error: str,
    trace: list | None = None, duration_ms: int | None = None,
) -> None:
    """Mark a turn failed or cancelled. No partial answer text is kept."""
    session.rollback()
    now = _now()
    session.execute(
        update(ChatMessage).where(ChatMessage.id == assistant_id)
        .values(status=status, content="", error=error[:1000], completed_at=now)
    )
    session.execute(
        update(InvestigationRun).where(InvestigationRun.id == run_id)
        .values(status=status, completed_at=now, duration_ms=duration_ms,
                trace=json.dumps(trace) if trace else None)
    )
    session.commit()


def recover_interrupted_messages(session: Session) -> int:
    """A server stopped mid-answer leaves ``generating`` rows; report them as failed."""
    now = _now()
    message_count = session.execute(
        update(ChatMessage).where(ChatMessage.status == GENERATING)
        .values(status=FAILED, error="The server stopped before the answer was completed.",
                completed_at=now)
    ).rowcount
    session.execute(
        update(InvestigationRun).where(InvestigationRun.status == "running")
        .values(status=FAILED, completed_at=now)
    )
    session.commit()
    return message_count
