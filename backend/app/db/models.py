from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="created", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    file_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_lines: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Code analysis (Milestone 2): pending -> ready | failed
    analysis_status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    symbol_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Incremented by every successful analysis; a search index built from an older revision
    # is stale (Milestone 3).
    analysis_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class ProjectFile(Base):
    __tablename__ = "project_files"
    __table_args__ = (UniqueConstraint("project_id", "path", name="uq_project_files_path"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    language: Mapped[str] = mapped_column(String(30), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    parser: Mapped[str | None] = mapped_column(String(40), nullable=True)
    parse_error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class CodeSymbol(Base):
    __tablename__ = "code_symbols"
    __table_args__ = (Index("ix_code_symbols_project_path", "project_id", "path"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    qualified_name: Mapped[str] = mapped_column(String(1024), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    parent: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    signature: Mapped[str | None] = mapped_column(String(500), nullable=True)
    docstring: Mapped[str | None] = mapped_column(String(500), nullable=True)


class CodeChunk(Base):
    __tablename__ = "code_chunks"
    __table_args__ = (
        UniqueConstraint("project_id", "path", "chunk_index", name="uq_code_chunks_index"),
        Index("ix_code_chunks_project_path", "project_id", "path"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    language: Mapped[str] = mapped_column(String(30), nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class SearchIndex(Base):
    """Search index state for one project (Milestone 3). Vectors live in ChromaDB only."""

    __tablename__ = "search_indexes"

    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    # pending -> indexing -> ready | failed. "stale" is derived when read (see indexing.status).
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    embedding_provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedding_dimension: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    collection_name: Mapped[str | None] = mapped_column(String(63), nullable=True)
    # Analysis revision the indexed entries were built from.
    analysis_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Last run: real counters, committed per batch while indexing.
    total_chunks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed_chunks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    embedded_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reused_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    removed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class IndexEntry(Base):
    """Manifest of indexed chunks. ``id`` is also the rowid in the ``chunk_fts`` FTS5 table, so
    ids are never reused (AUTOINCREMENT). ``record_id`` is the ChromaDB record id."""

    __tablename__ = "index_entries"
    __table_args__ = (
        UniqueConstraint("project_id", "record_id", name="uq_index_entries_record"),
        Index("ix_index_entries_project_path", "project_id", "path"),
        Index("ix_index_entries_project_embed", "project_id", "embed_hash"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    record_id: Mapped[str] = mapped_column(String(64), nullable=False)
    embed_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Kind of the chunk's symbol from code_symbols (function, class, method, ...), if any.
    symbol_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    language: Mapped[str] = mapped_column(String(30), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class Conversation(Base):
    """A repository-specific chat (Milestone 4)."""

    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_project_updated", "project_id", "updated_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChatMessage(Base):
    """One user question or assistant answer. Assistant rows start as ``generating`` and end as
    ``completed``, ``failed`` or ``cancelled``; only completed answers are shown as answers or
    reused as conversation context."""

    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_conversation", "conversation_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="completed")
    mode: Mapped[str | None] = mapped_column(String(20), nullable=True)  # baseline|investigation
    provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model_identifier: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    warnings: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list of strings
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Project analysis revision the answer's evidence was read from (staleness check).
    analysis_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MessageCitation(Base):
    """A verified citation of an assistant message: evidence ID -> exact source lines."""

    __tablename__ = "message_citations"
    __table_args__ = (Index("ix_message_citations_message", "message_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False
    )
    evidence_id: Mapped[str] = mapped_column(String(10), nullable=False)
    file_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("project_files.id", ondelete="SET NULL"), nullable=True
    )
    chunk_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    source_revision: Mapped[str] = mapped_column(String(40), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class InvestigationRun(Base):
    """What actually ran to produce an answer: mode, counters and the executed-step trace."""

    __tablename__ = "investigation_runs"
    __table_args__ = (Index("ix_investigation_runs_conversation", "conversation_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    user_message_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False
    )
    assistant_message_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    planner: Mapped[str | None] = mapped_column(String(20), nullable=True)
    tool_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    llm_request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trace: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list of executed steps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
