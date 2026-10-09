"""Safe extraction and scanning of untrusted repository archives.

Imported code is only ever written to disk and read back as text. Nothing is executed.
"""
import hashlib
import re
import shutil
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import Project, ProjectFile


class IngestionError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


LANGUAGES_BY_EXTENSION = {
    ".py": "Python",
    ".js": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".mts": "TypeScript",
    ".cts": "TypeScript",
    ".jsx": "JSX",
    ".tsx": "TSX",
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".md": "Markdown",
    ".markdown": "Markdown",
    ".sql": "SQL",
    ".toml": "TOML",
}

EXCLUDED_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
}

SENSITIVE_NAMES = {
    "credentials.json", "secrets.json", "secrets.yaml", "secrets.yml", "secrets.toml",
    "client_secret.json", ".npmrc", ".pypirc", ".netrc", ".htpasswd",
}
SENSITIVE_PATTERNS = (re.compile(r"^\.env(\..*)?$"), re.compile(r"^service[-_]?account.*\.json$"))

WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{i}" for prefix in ("COM", "LPT") for i in range(10)
}
MAX_PATH_LENGTH = 1024


@dataclass
class ScannedFile:
    path: str
    language: str
    size_bytes: int
    line_count: int
    content_hash: str


@dataclass
class ScanResult:
    files: list[ScannedFile] = field(default_factory=list)
    skipped: int = 0


def detect_language(filename: str) -> str | None:
    return LANGUAGES_BY_EXTENSION.get(PurePosixPath(filename).suffix.lower())


def is_sensitive(filename: str) -> bool:
    lowered = filename.lower()
    return lowered in SENSITIVE_NAMES or any(p.match(lowered) for p in SENSITIVE_PATTERNS)


def safe_parts(raw_name: str) -> list[str]:
    """Split an archive member name into safe path parts, rejecting traversal."""
    name = raw_name.replace("\\", "/")
    if "\x00" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise IngestionError(f"Archive contains an unsafe absolute path: {raw_name!r}")
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if any(p == ".." or ":" in p for p in parts):
        raise IngestionError(f"Archive contains an unsafe path: {raw_name!r}")
    return parts


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def _has_reserved_name(parts: list[str]) -> bool:
    return any(p.split(".")[0].upper() in WINDOWS_RESERVED for p in parts)


def count_lines(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def extract_archive(source: str | Path | BinaryIO, dest: Path, settings: Settings) -> ScanResult:
    """Extract only supported, non-sensitive text files from a ZIP into ``dest``."""
    dest_root = dest.resolve()
    try:
        archive = zipfile.ZipFile(source)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError) as exc:
        raise IngestionError("The file is not a valid ZIP archive.") from exc

    with archive:
        infos = archive.infolist()
        if len(infos) > settings.max_archive_entries:
            raise IngestionError(
                f"Archive has too many entries (limit {settings.max_archive_entries}).", 413
            )
        members: list[tuple[zipfile.ZipInfo, list[str]]] = []
        for info in infos:
            if info.flag_bits & 0x1:
                raise IngestionError("Encrypted ZIP archives are not supported.")
            members.append((info, safe_parts(info.filename)))

        # GitHub archives (and many user ZIPs) wrap everything in one top-level folder.
        file_members = [(i, p) for i, p in members if not i.is_dir() and p]
        roots = {p[0] for _, p in file_members}
        strip = 1 if len(roots) == 1 and all(len(p) > 1 for _, p in file_members) else 0

        result = ScanResult()
        extracted_total = 0
        for info, parts in file_members:
            rel_parts = parts[strip:]
            filename = rel_parts[-1]
            rel_path = "/".join(rel_parts)
            language = detect_language(filename)
            if (
                _is_symlink(info)
                or any(p in EXCLUDED_DIRS for p in rel_parts[:-1])
                or language is None
                or is_sensitive(filename)
                or info.file_size > settings.max_indexed_file_bytes
                or len(rel_path) > MAX_PATH_LENGTH
                or _has_reserved_name(rel_parts)
            ):
                result.skipped += 1
                continue

            target = dest_root.joinpath(*rel_parts).resolve()
            if not target.is_relative_to(dest_root):
                raise IngestionError(f"Archive contains an unsafe path: {info.filename!r}")

            try:
                with archive.open(info) as member:
                    # Never trust header sizes: read at most limit + 1 bytes.
                    data = member.read(settings.max_indexed_file_bytes + 1)
            except (zipfile.BadZipFile, RuntimeError, OSError, EOFError, ValueError) as exc:
                raise IngestionError("The ZIP archive is corrupted or malformed.") from exc
            if len(data) > settings.max_indexed_file_bytes:
                result.skipped += 1
                continue
            extracted_total += len(data)
            if extracted_total > settings.max_extracted_bytes:
                raise IngestionError(
                    "Archive exceeds the maximum extracted size "
                    f"({settings.max_extracted_bytes // (1024 * 1024)} MB).",
                    413,
                )
            if b"\x00" in data:
                result.skipped += 1
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                result.skipped += 1
                continue

            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                with open(target, "xb") as out:  # "x": never overwrite an earlier entry
                    out.write(data)
            except (FileExistsError, NotADirectoryError):
                result.skipped += 1  # duplicate or case-colliding entry
                continue

            result.files.append(
                ScannedFile(
                    path=rel_path,
                    language=language,
                    size_bytes=len(data),
                    line_count=count_lines(text),
                    content_hash=hashlib.sha256(data).hexdigest(),
                )
            )
    return result


def workspace_root(settings: Settings) -> Path:
    root = Path(settings.workspace_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def project_dir(settings: Settings, project_id: str) -> Path:
    UUID(project_id)  # raises ValueError for anything that is not a UUID
    root = workspace_root(settings)
    path = (root / project_id).resolve()
    if path.parent != root:
        raise ValueError("Invalid project directory")
    return path


def import_archive(
    session: Session,
    settings: Settings,
    source: str | Path | BinaryIO,
    *,
    name: str,
    source_type: str,
    source_url: str | None = None,
) -> Project:
    """Extract, scan and persist a project. Cleans up disk and DB state on any failure."""
    project_id = str(uuid4())
    dest = project_dir(settings, project_id)
    dest.mkdir()
    try:
        result = extract_archive(source, dest, settings)
        if not result.files:
            raise IngestionError("No supported source files were found in the archive.", 422)
        project = Project(
            id=project_id,
            name=name[:255],
            source_type=source_type,
            source_url=source_url,
            status="ready",
            file_count=len(result.files),
            total_lines=sum(f.line_count for f in result.files),
            total_bytes=sum(f.size_bytes for f in result.files),
            skipped_count=result.skipped,
        )
        session.add(project)
        session.add_all(
            ProjectFile(project_id=project_id, **vars(scanned)) for scanned in result.files
        )
        session.commit()
    except BaseException:
        session.rollback()
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return project


def read_project_file(settings: Settings, project_id: str, rel_path: str) -> str:
    root = project_dir(settings, project_id)
    target = root.joinpath(*safe_parts(rel_path)).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise IngestionError("File not found.", 404)
    return target.read_bytes().decode("utf-8", errors="replace")


def delete_project_files(settings: Settings, project_id: str) -> None:
    shutil.rmtree(project_dir(settings, project_id), ignore_errors=True)
