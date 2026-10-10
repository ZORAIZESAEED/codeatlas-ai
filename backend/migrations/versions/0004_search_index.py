"""Add search indexing: index state, chunk manifest and the FTS5 keyword index.

Revision ID: 0004_search_index
Revises: 0003_code_analysis
"""
import sqlite3

from alembic import op
import sqlalchemy as sa

revision = "0004_search_index"
down_revision = "0003_code_analysis"
branch_labels = None
depends_on = None


def upgrade():
    # Additive only: existing projects, files, symbols and chunks are untouched.
    op.add_column(
        "projects",
        sa.Column("analysis_revision", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "search_indexes",
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("embedding_provider", sa.String(length=40), nullable=True),
        sa.Column("embedding_model", sa.String(length=255), nullable=True),
        sa.Column("embedding_dimension", sa.Integer(), nullable=True),
        sa.Column("input_version", sa.String(length=20), nullable=True),
        sa.Column("collection_name", sa.String(length=63), nullable=True),
        sa.Column("analysis_revision", sa.Integer(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("embedded_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reused_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("removed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.String(length=1000), nullable=True),
    )

    op.create_table(
        "index_entries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("record_id", sa.String(length=64), nullable=False),
        sa.Column("embed_hash", sa.String(length=64), nullable=False),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("chunk_kind", sa.String(length=20), nullable=False),
        sa.Column("symbol", sa.String(length=1024), nullable=True),
        sa.Column("symbol_kind", sa.String(length=20), nullable=True),
        sa.Column("language", sa.String(length=30), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.UniqueConstraint("project_id", "record_id", name="uq_index_entries_record"),
        # Entry ids double as FTS rowids and must never be reused.
        sqlite_autoincrement=True,
    )
    op.create_index("ix_index_entries_project_path", "index_entries", ["project_id", "path"])
    op.create_index(
        "ix_index_entries_project_embed", "index_entries", ["project_id", "embed_hash"]
    )

    # FTS5 keyword index; rowid = index_entries.id. "_" is a token character so snake_case
    # identifiers also match exactly; split sub-words are indexed alongside them by the indexer.
    # SQLite >= 3.43 supports contentless tables with DELETE (tokens only, no source copy).
    # Older SQLite (e.g. some Docker base images) gets a regular FTS5 table, which supports
    # the same INSERT / DELETE / MATCH / bm25 statements but also stores the indexed text.
    contentless = sqlite3.sqlite_version_info >= (3, 43, 0)
    options = "content='', contentless_delete=1, " if contentless else ""
    op.execute(
        "CREATE VIRTUAL TABLE chunk_fts USING fts5("
        f"project, path, symbol, body, {options}"
        "tokenize=\"unicode61 remove_diacritics 2 tokenchars '_'\")"
    )


def downgrade():
    op.execute("DROP TABLE IF EXISTS chunk_fts")
    op.drop_index("ix_index_entries_project_embed", table_name="index_entries")
    op.drop_index("ix_index_entries_project_path", table_name="index_entries")
    op.drop_table("index_entries")
    op.drop_table("search_indexes")
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("analysis_revision")
