"""Project code analysis: parse every manifest file, extract symbols and build chunks.

Results replace any previous analysis of the project in a single transaction, so
re-running analysis is idempotent and an interrupted run leaves the previous results intact.
Imported files and the manifest are never modified except for the per-file ``parser`` /
``parse_error`` fields, which are written in that same transaction.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import CodeChunk, CodeSymbol, Project, ProjectFile
from app.services.chunking import Chunk, chunk_source
from app.services.ingestion import IngestionError, read_project_file
from app.services.parsing import FALLBACK_PARSER, PARSERS, ParseResult, parse_source

logger = logging.getLogger(__name__)


def _clip(value: str | None, size: int) -> str | None:
    return value[:size] if value else value


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _analyze_file(
    record: ProjectFile, text: str, settings: Settings, skip_reason: str | None
) -> tuple[ParseResult, list[Chunk]]:
    """Parse and chunk one file. Never raises: failures degrade to line chunking."""
    try:
        if skip_reason:
            result = ParseResult(parser=FALLBACK_PARSER, error=skip_reason)
        else:
            result = parse_source(text, record.language)
        chunks = chunk_source(
            text,
            record.language,
            result.symbols,
            settings.chunk_max_lines,
            settings.chunk_max_chars,
            structured=result.parser != FALLBACK_PARSER,
        )
        return result, chunks
    except Exception as exc:  # one file must never fail the whole project
        logger.warning("Analysis of %s failed: %r", record.path, exc)
        result = ParseResult(
            parser=FALLBACK_PARSER,
            error=f"Analysis failed for this file ({type(exc).__name__}); line chunking used.",
        )
        try:
            chunks = chunk_source(
                text, record.language, [], settings.chunk_max_lines, settings.chunk_max_chars
            )
        except Exception:
            logger.exception("Line chunking of %s failed", record.path)
            result.error = "Analysis failed for this file; no chunks were created."
            chunks = []
        return result, chunks


def analyze_project(session: Session, settings: Settings, project: Project) -> Project:
    files = session.scalars(
        select(ProjectFile).where(ProjectFile.project_id == project.id).order_by(ProjectFile.path)
    ).all()
    symbols: list[CodeSymbol] = []
    chunks: list[CodeChunk] = []
    parse_budget = settings.parse_max_total_bytes

    for record in files:
        try:
            text = read_project_file(settings, project.id, record.path)
        except (IngestionError, OSError):
            record.parser = None
            record.parse_error = "File is missing from the project workspace."
            continue

        # Structural parsing is bounded per file and per project; line chunking is linear
        # and always runs.
        skip_reason = None
        if record.language in PARSERS:
            if record.size_bytes > settings.parse_max_file_bytes:
                skip_reason = (
                    f"File exceeds the structural parsing limit "
                    f"({settings.parse_max_file_bytes // 1024} KB); line chunking used."
                )
            elif record.size_bytes > parse_budget:
                skip_reason = "Project exceeds the structural parsing budget; line chunking used."
            else:
                parse_budget -= record.size_bytes

        result, file_chunks = _analyze_file(record, text, settings, skip_reason)
        record.parser = result.parser
        record.parse_error = _clip(result.error, 500)

        for sym in result.symbols:
            symbols.append(
                CodeSymbol(
                    project_id=project.id,
                    path=record.path,
                    name=sym.name[:255],
                    qualified_name=sym.qualified_name[:1024],
                    kind=sym.kind,
                    parent=_clip(sym.parent, 1024),
                    start_line=sym.start_line,
                    end_line=sym.end_line,
                    signature=_clip(sym.signature, 500),
                    docstring=_clip(sym.docstring, 500),
                )
            )
        for index, chunk in enumerate(file_chunks):
            chunks.append(
                CodeChunk(
                    project_id=project.id,
                    path=record.path,
                    chunk_index=index,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    kind=chunk.kind,
                    symbol=_clip(chunk.symbol, 1024),
                    language=record.language,
                    char_count=len(chunk.text),
                    content_hash=chunk.content_hash,
                )
            )

    # Replace previous results atomically: nothing below is visible until commit.
    session.execute(delete(CodeSymbol).where(CodeSymbol.project_id == project.id))
    session.execute(delete(CodeChunk).where(CodeChunk.project_id == project.id))
    session.add_all(symbols)
    session.add_all(chunks)
    project.symbol_count = len(symbols)
    project.chunk_count = len(chunks)
    project.analysis_status = "ready"
    project.analyzed_at = _now()
    project.analysis_revision = (project.analysis_revision or 0) + 1  # stales search indexes
    session.commit()
    return project


def run_analysis(session: Session, settings: Settings, project: Project) -> Project | None:
    """Analyze a project. On an unexpected failure the transaction is rolled back (previous
    results and the import stay intact) and the project is marked ``failed``.

    Returns ``None`` if the project no longer exists (e.g. deleted during analysis).
    """
    project_id = project.id
    try:
        return analyze_project(session, settings, project)
    except Exception:
        logger.exception("Code analysis failed for project %s", project_id)
        session.rollback()
    try:
        current = session.get(Project, project_id)
        if current is None:
            return None
        current.analysis_status = "failed"
        session.commit()
        return current
    except Exception:
        logger.exception("Could not record analysis failure for project %s", project_id)
        session.rollback()
        return None


def delete_analysis(session: Session, project_id: str) -> None:
    """Remove a project's symbols and chunks (caller commits)."""
    session.execute(delete(CodeSymbol).where(CodeSymbol.project_id == project_id))
    session.execute(delete(CodeChunk).where(CodeChunk.project_id == project_id))
