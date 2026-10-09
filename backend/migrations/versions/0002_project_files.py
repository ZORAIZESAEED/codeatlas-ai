"""Add ingestion metadata to projects and create project_files manifest.

Revision ID: 0002_project_files
Revises: 0001_initial
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_project_files"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    # Additive only: existing project rows are preserved and receive defaults.
    op.add_column("projects", sa.Column("source_url", sa.String(length=500), nullable=True))
    for column in ("file_count", "total_lines", "total_bytes", "skipped_count"):
        op.add_column(
            "projects", sa.Column(column, sa.Integer(), nullable=False, server_default="0")
        )
    op.create_table(
        "project_files",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("language", sa.String(length=30), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.UniqueConstraint("project_id", "path", name="uq_project_files_path"),
    )
    op.create_index("ix_project_files_project_id", "project_files", ["project_id"])


def downgrade():
    op.drop_index("ix_project_files_project_id", table_name="project_files")
    op.drop_table("project_files")
    with op.batch_alter_table("projects") as batch:
        for column in ("skipped_count", "total_bytes", "total_lines", "file_count", "source_url"):
            batch.drop_column(column)
