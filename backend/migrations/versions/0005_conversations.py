"""Add AI chat: conversations, messages, verified citations and investigation runs.

Revision ID: 0005_conversations
Revises: 0004_search_index
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_conversations"
down_revision = "0004_search_index"
branch_labels = None
depends_on = None


def upgrade():
    # Additive only: no existing table or row is changed.
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_conversations_project_updated", "conversations", ["project_id", "updated_at"]
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(length=36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="completed"),
        sa.Column("mode", sa.String(length=20), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=True),
        sa.Column("model_identifier", sa.String(length=255), nullable=True),
        sa.Column("error", sa.String(length=1000), nullable=True),
        sa.Column("warnings", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("analysis_revision", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_chat_messages_conversation", "chat_messages", ["conversation_id", "created_at"]
    )

    op.create_table(
        "message_citations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "message_id",
            sa.String(length=36),
            sa.ForeignKey("chat_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("evidence_id", sa.String(length=10), nullable=False),
        sa.Column(
            "file_id",
            sa.Integer(),
            sa.ForeignKey("project_files.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("chunk_id", sa.String(length=64), nullable=True),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=1024), nullable=True),
        sa.Column("source_revision", sa.String(length=40), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_message_citations_message", "message_citations", ["message_id"])

    op.create_table(
        "investigation_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(length=36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_message_id",
            sa.String(length=36),
            sa.ForeignKey("chat_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "assistant_message_id",
            sa.String(length=36),
            sa.ForeignKey("chat_messages.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="running"),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("planner", sa.String(length=20), nullable=True),
        sa.Column("tool_call_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("llm_request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("trace", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_investigation_runs_conversation", "investigation_runs", ["conversation_id"]
    )


def downgrade():
    op.drop_index("ix_investigation_runs_conversation", table_name="investigation_runs")
    op.drop_table("investigation_runs")
    op.drop_index("ix_message_citations_message", table_name="message_citations")
    op.drop_table("message_citations")
    op.drop_index("ix_chat_messages_conversation", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("ix_conversations_project_updated", table_name="conversations")
    op.drop_table("conversations")
