"""Add code analysis: symbols, chunks and per-project/per-file analysis metadata.

Revision ID: 0003_code_analysis
Revises: 0002_project_files
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_code_analysis"
down_revision = "0002_project_files"
branch_labels = None
depends_on = None


def upgrade():
    # Additive only: existing projects keep their files and become "pending" analysis.
    op.add_column(
        "projects",
        sa.Column("analysis_status", sa.String(length=20), nullable=False, server_default="pending"),
    )
    op.add_column("projects", sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=True))
    for column in ("symbol_count", "chunk_count"):
        op.add_column(
            "projects", sa.Column(column, sa.Integer(), nullable=False, server_default="0")
        )
    op.add_column("project_files", sa.Column("parser", sa.String(length=40), nullable=True))
    op.add_column("project_files", sa.Column("parse_error", sa.String(length=500), nullable=True))

    op.create_table(
        "code_symbols",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("qualified_name", sa.String(length=1024), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("parent", sa.String(length=1024), nullable=True),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("signature", sa.String(length=500), nullable=True),
        sa.Column("docstring", sa.String(length=500), nullable=True),
    )
    op.create_index("ix_code_symbols_project_path", "code_symbols", ["project_id", "path"])

    op.create_table(
        "code_chunks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("symbol", sa.String(length=1024), nullable=True),
        sa.Column("language", sa.String(length=30), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.UniqueConstraint("project_id", "path", "chunk_index", name="uq_code_chunks_index"),
    )
    op.create_index("ix_code_chunks_project_path", "code_chunks", ["project_id", "path"])


def downgrade():
    op.drop_index("ix_code_chunks_project_path", table_name="code_chunks")
    op.drop_table("code_chunks")
    op.drop_index("ix_code_symbols_project_path", table_name="code_symbols")
    op.drop_table("code_symbols")
    with op.batch_alter_table("project_files") as batch:
        batch.drop_column("parse_error")
        batch.drop_column("parser")
    with op.batch_alter_table("projects") as batch:
        for column in ("chunk_count", "symbol_count", "analyzed_at", "analysis_status"):
            batch.drop_column(column)
