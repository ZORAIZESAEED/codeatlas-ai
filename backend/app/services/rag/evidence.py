"""Evidence records: verified source ranges that the model may cite as E1, E2, ...

Every evidence record is created by the backend from a manifest-checked source read (a validated
search hit or a bounded read tool). The model only ever sees IDs the backend issued, and every
ID maps back to a project, a file record, a line range and the hash of those exact lines.
"""
import hashlib
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import Project, ProjectFile
from app.services.chunking import split_lines
from app.services.ingestion import IngestionError, read_project_file
from app.services.retrieval.models import Candidate


def lines_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Evidence:
    id: str
    project_id: str
    file_id: int
    path: str
    language: str
    start_line: int
    end_line: int
    text: str
    content_hash: str  # sha256 of the exact lines start..end
    analysis_revision: int
    symbol: str | None = None
    symbol_kind: str | None = None
    chunk_id: str | None = None  # index entry record id, when the evidence is a search hit
    methods: list[str] = field(default_factory=list)  # exact / keyword / semantic / tool name
    provenance: str = ""  # what produced it, e.g. "search: <query>" or "read_file"
    truncated: bool = False  # lines were cut to fit the context budget

    @property
    def source_revision(self) -> str:
        return f"r{self.analysis_revision}:{self.content_hash[:16]}"

    @property
    def reference(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"

    def truncate(self, keep_lines: int) -> None:
        """Keep only the first lines (to fit the context budget). The cited range and hash then
        describe exactly the lines the model saw."""
        lines = split_lines(self.text)
        if keep_lines < 1 or keep_lines >= len(lines):
            return
        self.text = "".join(lines[:keep_lines])
        self.end_line = self.start_line + keep_lines - 1
        self.content_hash = lines_hash(self.text)
        self.truncated = True


class EvidenceError(Exception):
    pass


class EvidenceSet:
    """Issues stable IDs in insertion order and drops ranges already covered by earlier evidence."""

    def __init__(self, session: Session, settings: Settings, project: Project) -> None:
        self.session = session
        self.settings = settings
        self.project = project
        self.items: list[Evidence] = []
        self._files: dict[str, ProjectFile | None] = {}
        self._lines: dict[str, list[str]] = {}

    def __len__(self) -> int:
        return len(self.items)

    def get(self, evidence_id: str) -> Evidence | None:
        return next((e for e in self.items if e.id == evidence_id), None)

    def file_record(self, path: str) -> ProjectFile | None:
        if path not in self._files:
            self._files[path] = self.session.scalar(
                select(ProjectFile).where(
                    ProjectFile.project_id == self.project.id, ProjectFile.path == path
                )
            )
        return self._files[path]

    def file_lines(self, path: str) -> list[str]:
        """Current lines of a manifest file. Raises EvidenceError for anything not in the
        manifest; reads go through the workspace-confined reader."""
        if path not in self._lines:
            if self.file_record(path) is None:
                raise EvidenceError(f"'{path}' is not a file of this project.")
            try:
                text = read_project_file(self.settings, self.project.id, path)
            except (IngestionError, OSError) as exc:
                raise EvidenceError(f"'{path}' could not be read.") from exc
            self._lines[path] = split_lines(text)
        return self._lines[path]

    def covering(self, path: str, start: int, end: int) -> Evidence | None:
        return next(
            (e for e in self.items if e.path == path and e.start_line <= start
             and end <= e.end_line),
            None,
        )

    def add_range(
        self,
        path: str,
        start: int,
        end: int,
        *,
        methods: list[str],
        provenance: str,
        symbol: str | None = None,
        symbol_kind: str | None = None,
        chunk_id: str | None = None,
        expected_hash: str | None = None,
    ) -> tuple[Evidence, bool]:
        """Add verified lines. Returns (evidence, created). Existing coverage is reused."""
        existing = self.covering(path, start, end)
        if existing is not None:
            for m in methods:
                if m not in existing.methods:
                    existing.methods.append(m)
            return existing, False
        lines = self.file_lines(path)
        if start < 1 or end < start or end > len(lines):
            raise EvidenceError(f"Lines {start}-{end} are outside '{path}' "
                                f"({len(lines)} lines).")
        text = "".join(lines[start - 1:end])
        digest = lines_hash(text)
        if expected_hash is not None and digest != expected_hash:
            raise EvidenceError(f"'{path}' changed since it was indexed.")
        record = self.file_record(path)
        evidence = Evidence(
            id=f"E{len(self.items) + 1}", project_id=self.project.id, file_id=record.id,
            path=path, language=record.language, start_line=start, end_line=end, text=text,
            content_hash=digest, analysis_revision=self.project.analysis_revision,
            symbol=symbol, symbol_kind=symbol_kind, chunk_id=chunk_id, methods=list(methods),
            provenance=provenance,
        )
        self.items.append(evidence)
        return evidence, True

    def add_candidate(self, candidate: Candidate, provenance: str) -> tuple[Evidence, bool]:
        e = candidate.entry
        return self.add_range(
            e.path, e.start_line, e.end_line, methods=candidate.methods, provenance=provenance,
            symbol=e.symbol, symbol_kind=e.symbol_kind, chunk_id=e.record_id,
            expected_hash=e.content_hash,
        )
