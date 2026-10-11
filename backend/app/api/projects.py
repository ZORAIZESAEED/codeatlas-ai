import tempfile
from collections import defaultdict
from collections.abc import Iterator, Sequence
from pathlib import PurePosixPath

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.core.config import Settings
from app.db.models import CodeChunk, CodeSymbol, Project, ProjectFile, SearchIndex
from app.db.session import get_db
from app.schemas import (
    AnalysisOverview,
    ChunkListResponse,
    ChunkOut,
    FileContentResponse,
    FileListResponse,
    FileParseIssue,
    GitHubImportRequest,
    LanguageStat,
    ProjectFileOut,
    ProjectOverview,
    ProjectSummary,
    SymbolListResponse,
    SymbolOut,
    TopLevelEntry,
)
from app.services.analysis import delete_analysis, run_analysis
from app.services.chunking import split_lines
from app.services.conversations import delete_project_conversations
from app.services.github import download_repository, parse_github_url
from app.services.indexing.indexer import drop_project_vectors
from app.services.indexing.manifest import delete_project_index
from app.services.ingestion import (
    IngestionError,
    delete_project_files,
    import_archive,
    read_project_file,
)

router = APIRouter(prefix="/api/projects", tags=["projects"])

MULTIPART_OVERHEAD = 64 * 1024


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_github_client(request: Request) -> Iterator[httpx.Client]:
    settings: Settings = request.app.state.settings
    with httpx.Client(
        timeout=settings.github_timeout_seconds,
        follow_redirects=False,
        headers={"User-Agent": "CodeAtlas-AI", "Accept": "application/vnd.github+json"},
    ) as client:
        yield client


def _languages_by_project(db: Session, project_ids: list[str]) -> dict[str, list[str]]:
    if not project_ids:
        return {}
    rows = db.execute(
        select(ProjectFile.project_id, ProjectFile.language, func.count())
        .where(ProjectFile.project_id.in_(project_ids))
        .group_by(ProjectFile.project_id, ProjectFile.language)
    ).all()
    counts: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for project_id, language, count in rows:
        counts[project_id].append((count, language))
    return {pid: [lang for _, lang in sorted(items, reverse=True)] for pid, items in counts.items()}


def _summary(db: Session, project: Project) -> ProjectSummary:
    summary = ProjectSummary.model_validate(project)
    summary.languages = _languages_by_project(db, [project.id]).get(project.id, [])
    return summary


def _analyzed_summary(db: Session, project: Project | None) -> ProjectSummary:
    # Analysis failures never fail an import: the project stays importable and browsable with
    # analysis_status="failed". Only a project deleted mid-analysis has nothing to return.
    if project is None:
        raise HTTPException(status_code=404, detail="Project was deleted during analysis.")
    return _summary(db, project)


def _get_project(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    return project


def _clean_name(raw: str, fallback: str) -> str:
    name = " ".join("".join(ch for ch in raw if ch.isprintable()).split())
    return (name or fallback)[:255]


@router.get("", response_model=list[ProjectSummary])
def list_projects(db: Session = Depends(get_db)) -> list[ProjectSummary]:
    projects = db.scalars(select(Project).order_by(Project.created_at.desc())).all()
    languages = _languages_by_project(db, [p.id for p in projects])
    result = []
    for project in projects:
        summary = ProjectSummary.model_validate(project)
        summary.languages = languages.get(project.id, [])
        result.append(summary)
    return result


@router.get("/{project_id}", response_model=ProjectSummary)
def get_project(project_id: str, db: Session = Depends(get_db)) -> ProjectSummary:
    return _summary(db, _get_project(db, project_id))


@router.delete("/{project_id}", status_code=204)
def delete_project(
    project_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    project = _get_project(db, project_id)
    was_indexed = db.get(SearchIndex, project.id) is not None
    delete_project_conversations(db, project.id)
    delete_project_index(db, project.id)
    delete_analysis(db, project.id)
    db.execute(delete(ProjectFile).where(ProjectFile.project_id == project.id))
    db.delete(project)
    db.commit()
    # Files and vectors are removed after the DB commit so a failed commit never loses data.
    delete_project_files(settings, project.id)
    if was_indexed:
        drop_project_vectors(settings, project.id)
    return Response(status_code=204)


@router.post("/import/github", response_model=ProjectSummary, status_code=201)
def import_github(
    payload: GitHubImportRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    client: httpx.Client = Depends(get_github_client),
) -> ProjectSummary:
    owner, repo = parse_github_url(payload.url)
    with tempfile.TemporaryFile() as archive:
        info = download_repository(client, owner, repo, archive, settings.max_download_bytes)
        project = import_archive(
            db, settings, archive, name=info.full_name, source_type="github",
            source_url=info.html_url,
        )
    return _analyzed_summary(db, run_analysis(db, settings, project))


@router.post(
    "/import/zip",
    response_model=ProjectSummary,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {
                            "file": {"type": "string", "format": "binary"},
                            "name": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
async def import_zip(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> ProjectSummary:
    # Check the declared size before the multipart body is parsed and spooled to disk.
    length = request.headers.get("content-length", "")
    if not length.isdigit():
        raise HTTPException(status_code=411, detail="Content-Length header is required.")
    limit_mb = settings.max_upload_bytes // (1024 * 1024)
    if int(length) > settings.max_upload_bytes + MULTIPART_OVERHEAD:
        raise HTTPException(status_code=413, detail=f"ZIP upload exceeds {limit_mb} MB.")
    try:
        form = await request.form(max_files=1, max_fields=2)
    except Exception as exc:  # malformed multipart bodies
        raise HTTPException(status_code=400, detail="Invalid multipart form data.") from exc
    try:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise HTTPException(status_code=422, detail="A ZIP file is required in field 'file'.")
        filename = PurePosixPath((upload.filename or "").replace("\\", "/")).name
        if not filename.lower().endswith(".zip"):
            raise HTTPException(status_code=400, detail="Only .zip files are accepted.")
        if upload.size is not None and upload.size > settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail=f"ZIP upload exceeds {limit_mb} MB.")
        raw_name = form.get("name")
        name = _clean_name(
            raw_name if isinstance(raw_name, str) else "", filename[:-4] or "project"
        )
        project = await run_in_threadpool(
            import_archive, db, settings, upload.file, name=name, source_type="zip"
        )
    finally:
        await form.close()
    analyzed = await run_in_threadpool(run_analysis, db, settings, project)
    return _analyzed_summary(db, analyzed)


@router.get("/{project_id}/files", response_model=FileListResponse)
def list_files(project_id: str, db: Session = Depends(get_db)) -> FileListResponse:
    project = _get_project(db, project_id)
    files = db.scalars(
        select(ProjectFile).where(ProjectFile.project_id == project.id).order_by(ProjectFile.path)
    ).all()
    return FileListResponse(
        project_id=project.id, files=[ProjectFileOut.model_validate(f) for f in files]
    )


@router.get("/{project_id}/files/content", response_model=FileContentResponse)
def get_file_content(
    project_id: str,
    path: str = Query(min_length=1, max_length=1024),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> FileContentResponse:
    project = _get_project(db, project_id)
    # Only files recorded in the manifest can be read.
    record = db.scalar(
        select(ProjectFile).where(ProjectFile.project_id == project.id, ProjectFile.path == path)
    )
    if record is None:
        raise HTTPException(status_code=404, detail="File not found.")
    content = read_project_file(settings, project.id, record.path)
    return FileContentResponse(
        project_id=project.id,
        path=record.path,
        language=record.language,
        size_bytes=record.size_bytes,
        line_count=record.line_count,
        content=content,
    )


@router.get("/{project_id}/overview", response_model=ProjectOverview)
def get_overview(project_id: str, db: Session = Depends(get_db)) -> ProjectOverview:
    project = _get_project(db, project_id)
    files = db.scalars(select(ProjectFile).where(ProjectFile.project_id == project.id)).all()

    stats: dict[str, LanguageStat] = {}
    directories: set[str] = set()
    top_level: dict[str, TopLevelEntry] = {}
    for f in files:
        stat = stats.setdefault(
            f.language, LanguageStat(language=f.language, files=0, lines=0, bytes=0)
        )
        stat.files += 1
        stat.lines += f.line_count
        stat.bytes += f.size_bytes
        parts = f.path.split("/")
        directories.update("/".join(parts[:i]) for i in range(1, len(parts)))
        kind = "directory" if len(parts) > 1 else "file"
        entry = top_level.setdefault(
            parts[0], TopLevelEntry(name=parts[0], type=kind, file_count=0)
        )
        entry.file_count += 1

    largest = sorted(files, key=lambda f: f.size_bytes, reverse=True)[:5]
    return ProjectOverview(
        project=_summary(db, project),
        languages=sorted(stats.values(), key=lambda s: (-s.lines, s.language)),
        directory_count=len(directories),
        top_level=sorted(top_level.values(), key=lambda e: (e.type != "directory", e.name.lower())),
        largest_files=[ProjectFileOut.model_validate(f) for f in largest],
        analysis=_analysis_overview(db, project, files),
    )


MAX_ISSUES = 50


def _analysis_overview(
    db: Session, project: Project, files: Sequence[ProjectFile]
) -> AnalysisOverview:
    kinds = db.execute(
        select(CodeSymbol.kind, func.count())
        .where(CodeSymbol.project_id == project.id)
        .group_by(CodeSymbol.kind)
    ).all()
    parsers: dict[str, int] = defaultdict(int)
    issues = []
    for f in files:
        if f.parser:
            parsers[f.parser] += 1
        if f.parse_error:
            issues.append(FileParseIssue(path=f.path, parser=f.parser, parse_error=f.parse_error))
    issues.sort(key=lambda i: i.path)
    return AnalysisOverview(
        status=project.analysis_status,
        analyzed_at=ProjectSummary.assume_utc(project.analyzed_at),
        symbol_count=project.symbol_count,
        chunk_count=project.chunk_count,
        symbols_by_kind={kind: count for kind, count in sorted(kinds)},
        files_by_parser=dict(sorted(parsers.items())),
        issue_count=len(issues),
        issues=issues[:MAX_ISSUES],
    )


@router.post("/{project_id}/analyze", response_model=ProjectSummary)
def analyze(
    project_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> ProjectSummary:
    """Parse all project files and rebuild symbols and chunks (idempotent)."""
    return _analyzed_summary(db, run_analysis(db, settings, _get_project(db, project_id)))


@router.get("/{project_id}/symbols", response_model=SymbolListResponse)
def list_symbols(
    project_id: str,
    path: str | None = Query(default=None, max_length=1024),
    kind: str | None = Query(default=None, max_length=20),
    q: str | None = Query(default=None, max_length=200, description="Name contains (any case)"),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> SymbolListResponse:
    project = _get_project(db, project_id)
    query = select(CodeSymbol).where(CodeSymbol.project_id == project.id)
    if path:
        query = query.where(CodeSymbol.path == path)
    if kind:
        query = query.where(CodeSymbol.kind == kind)
    if q:
        query = query.where(func.lower(CodeSymbol.qualified_name).contains(q.lower()))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(CodeSymbol.path, CodeSymbol.start_line, CodeSymbol.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return SymbolListResponse(
        project_id=project.id, total=total, symbols=[SymbolOut.model_validate(r) for r in rows]
    )


@router.get("/{project_id}/chunks", response_model=ChunkListResponse)
def list_chunks(
    project_id: str,
    path: str | None = Query(default=None, max_length=1024),
    include_content: bool = Query(default=False, description="Requires `path`"),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> ChunkListResponse:
    project = _get_project(db, project_id)
    if include_content and not path:
        raise HTTPException(status_code=422, detail="include_content requires a file path.")
    query = select(CodeChunk).where(CodeChunk.project_id == project.id)
    if path:
        query = query.where(CodeChunk.path == path)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(CodeChunk.path, CodeChunk.chunk_index).limit(limit).offset(offset)
    ).all()
    chunks = [ChunkOut.model_validate(r) for r in rows]
    if include_content and rows:
        # Chunks only exist for manifest files, so the path is already validated.
        lines = split_lines(read_project_file(settings, project.id, rows[0].path))
        for chunk in chunks:
            chunk.content = "".join(lines[chunk.start_line - 1 : chunk.end_line])
    return ChunkListResponse(project_id=project.id, total=total, chunks=chunks)


def ingestion_error_handler(_: Request, exc: IngestionError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})
