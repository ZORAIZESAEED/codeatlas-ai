"""Read-only investigation tools.

Every tool is bound to one project when it is created; no argument can name another project,
an absolute path or a directory outside the manifest. Arguments are validated here,
independently of whoever produced them (the model or the deterministic planner). Source is read
only through the manifest-checked readers, and every tool call counts against hard limits.
Nothing here writes files, runs code, opens network connections or reads the environment.
"""
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, or_, select

from app.db.models import CodeSymbol, IndexEntry, ProjectFile
from app.services.llm.base import ToolSpec
from app.services.rag.answer import AnswerRequest, search_evidence
from app.services.rag.evidence import Evidence, EvidenceError, EvidenceSet
from app.services.rag.prompts import render_evidence
from app.services.retrieval.hybrid import MODES as SEARCH_MODES
from app.services.retrieval.models import SearchError, SearchFilters

MAX_READ_LINES = 150  # per read_file call
MAX_SYMBOL_LINES = 120  # evidence taken from one symbol definition
MAX_SYMBOL_MATCHES = 4
MAX_FILE_LIST = 200
MAX_SEARCH_RESULTS = 8


class ToolArgumentError(ValueError):
    pass


@dataclass
class ToolResult:
    ok: bool
    summary: str  # short factual description of what ran, safe to show to the user
    content: str  # what the model sees (evidence blocks are fenced as untrusted data)
    evidence: list[Evidence] = field(default_factory=list)
    lines_read: int = 0


@dataclass
class Limits:
    max_tool_calls: int
    max_lines_read: int
    max_evidence: int
    tool_calls: int = 0
    lines_read: int = 0


def _text(args: dict, key: str, *, required: bool = True, max_len: int = 300) -> str | None:
    value = args.get(key)
    if value is None:
        if required:
            raise ToolArgumentError(f"'{key}' is required.")
        return None
    if not isinstance(value, str):
        raise ToolArgumentError(f"'{key}' must be a string.")
    value = value.strip()
    if required and not value:
        raise ToolArgumentError(f"'{key}' must not be empty.")
    if len(value) > max_len or any(not ch.isprintable() for ch in value):
        raise ToolArgumentError(f"'{key}' is too long or contains control characters.")
    return value or None


def _int(args: dict, key: str, *, default: int | None = None, lo: int = 1,
         hi: int = 1_000_000) -> int:
    value = args.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value.strip())
        else:
            raise ToolArgumentError(f"'{key}' must be an integer.")
    if not lo <= value <= hi:
        raise ToolArgumentError(f"'{key}' must be between {lo} and {hi}.")
    return value


def _only(args: dict, allowed: set[str]) -> None:
    unknown = set(args) - allowed
    if unknown:
        raise ToolArgumentError(f"Unknown argument(s): {', '.join(sorted(unknown))}.")


def _like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


TOOL_SPECS = [
    ToolSpec("search_code", "Search this repository's index for source code relevant to a query.",
             {"type": "object", "properties": {
                 "query": {"type": "string", "description": "What to look for."},
                 "mode": {"type": "string", "enum": list(SEARCH_MODES)},
                 "language": {"type": "string"},
                 "directory": {"type": "string", "description": "Repository-relative folder."},
                 "limit": {"type": "integer", "minimum": 1, "maximum": MAX_SEARCH_RESULTS}},
              "required": ["query"]}),
    ToolSpec("read_file", f"Read lines of a repository file (at most {MAX_READ_LINES} lines).",
             {"type": "object", "properties": {
                 "path": {"type": "string", "description": "Repository-relative file path."},
                 "start_line": {"type": "integer", "minimum": 1},
                 "end_line": {"type": "integer", "minimum": 1}},
              "required": ["path", "start_line", "end_line"]}),
    ToolSpec("find_symbol", "Find the definition of a function, class or method by name.",
             {"type": "object", "properties": {
                 "name": {"type": "string", "description": "Name or qualified name."},
                 "path": {"type": "string", "description": "Optional file path to restrict to."}},
              "required": ["name"]}),
    ToolSpec("get_chunk", "Get one indexed source chunk by its chunk id.",
             {"type": "object", "properties": {"chunk_id": {"type": "string"}},
              "required": ["chunk_id"]}),
    ToolSpec("list_project_files", "List repository files, optionally under one directory.",
             {"type": "object", "properties": {"directory": {"type": "string"}}}),
    ToolSpec("get_project_overview", "Languages, top-level folders and symbol counts.",
             {"type": "object", "properties": {}}),
]
TOOL_NAMES = {t.name for t in TOOL_SPECS}


class InvestigationTools:
    def __init__(self, req: AnswerRequest, evidence: EvidenceSet, limits: Limits,
                 fence: str) -> None:
        self.req = req
        self.evidence = evidence
        self.limits = limits
        self.fence = fence
        self.project_id = req.project.id
        self._seen: set[tuple[str, str]] = set()
        self._reason: str | None = None

    def is_duplicate(self, name: str, args: dict[str, Any]) -> bool:
        return (name, _key(args)) in self._seen

    def execute(self, name: str, args: dict[str, Any], reason: str | None = None) -> ToolResult:
        """Validate and run one tool call. Never raises for bad input: errors become results
        the caller can record (and the model can read)."""
        if self.limits.tool_calls >= self.limits.max_tool_calls:
            return ToolResult(False, "Tool call limit reached", "Error: tool call limit reached.")
        key = (str(name), _key(args))
        if key in self._seen:
            return ToolResult(False, f"Skipped repeated {name}",
                              "Error: this exact call was already made; use its earlier result.")
        self._seen.add(key)
        # Why the planner asked for this; recorded with the evidence so the answer step can see
        # e.g. that a definition was fetched because it was imported, not because it is called.
        self._reason = reason
        # Every new attempt counts, including rejected ones, so invalid calls cannot loop.
        self.limits.tool_calls += 1
        if name not in TOOL_NAMES:
            return ToolResult(False, f"Rejected unknown tool '{str(name)[:40]}'",
                              f"Error: there is no tool named '{str(name)[:40]}'.")
        if not isinstance(args, dict):
            return ToolResult(False, f"Rejected {name}: invalid arguments",
                              "Error: arguments must be an object.")
        try:
            return getattr(self, f"_{name}")(args)
        except ToolArgumentError as exc:
            return ToolResult(False, f"Rejected {name}: {exc}", f"Error: {exc}")
        except EvidenceError as exc:
            return ToolResult(False, f"{name} failed: {exc}", f"Error: {exc}")

    # -- helpers ----------------------------------------------------------------------------
    def _why(self, provenance: str) -> str:
        return f"{provenance}; {self._reason}" if self._reason else provenance

    def _room(self) -> int:
        return self.limits.max_evidence - len(self.evidence)

    def _add(self, path: str, start: int, end: int, *, method: str, provenance: str,
             symbol: str | None = None, symbol_kind: str | None = None,
             chunk_id: str | None = None, expected_hash: str | None = None
             ) -> tuple[Evidence | None, bool, int]:
        """Add evidence within the line and evidence budgets. Returns (evidence, created,
        lines charged)."""
        existing = self.evidence.covering(path, start, end)
        if existing is not None:
            return existing, False, 0
        if self._room() <= 0:
            return None, False, 0
        budget = self.limits.max_lines_read - self.limits.lines_read
        if budget <= 0:
            return None, False, 0
        full_end, end = end, min(end, start + budget - 1)
        item, created = self.evidence.add_range(
            path, start, end, methods=[method], provenance=provenance, symbol=symbol,
            symbol_kind=symbol_kind, chunk_id=chunk_id,
            # The indexed hash covers the full range; it can only be checked when uncut.
            expected_hash=expected_hash if end == full_end else None,
        )
        if created and end < full_end:
            item.truncated = True
        lines = end - start + 1 if created else 0
        self.limits.lines_read += lines
        return item, created, lines

    def _blocks(self, items: list[Evidence]) -> str:
        return "\n\n".join(render_evidence(e, self.fence) for e in items)

    def _manifest_path(self, raw: str) -> ProjectFile:
        if raw.startswith(("/", "\\")) or ".." in raw.replace("\\", "/").split("/") or ":" in raw:
            raise ToolArgumentError("Paths must be repository-relative.")
        record = self.evidence.file_record(raw.replace("\\", "/"))
        if record is None:
            raise ToolArgumentError(f"'{raw}' is not a file of this project.")
        return record

    # -- tools ------------------------------------------------------------------------------
    def _search_code(self, args: dict) -> ToolResult:
        _only(args, {"query", "mode", "language", "directory", "limit"})
        query = _text(args, "query")
        mode = _text(args, "mode", required=False, max_len=20)
        if mode is not None and mode not in SEARCH_MODES:
            raise ToolArgumentError(f"'mode' must be one of {', '.join(SEARCH_MODES)}.")
        language = _text(args, "language", required=False, max_len=30)
        directory = _text(args, "directory", required=False, max_len=1024)
        if directory and (".." in directory.split("/") or directory.startswith("/")):
            raise ToolArgumentError("'directory' must be repository-relative.")
        limit = _int(args, "limit", default=5, hi=MAX_SEARCH_RESULTS)
        limit = min(limit, max(self._room(), 0))
        if limit <= 0:
            return ToolResult(False, "Evidence limit reached", "Error: evidence limit reached.")
        saved = self.req.retrieval_mode
        self.req.retrieval_mode = mode or saved
        try:
            added, info = search_evidence(
                self.req, self.evidence, query, limit=limit,
                filters=SearchFilters(language=language, directory=directory),
                provenance=self._why(f"search_code: {query}"),
            )
        except SearchError as exc:
            raise ToolArgumentError(exc.message) from exc
        finally:
            self.req.retrieval_mode = saved
        lines = sum(e.end_line - e.start_line + 1 for e in added)
        self.limits.lines_read += lines
        content = (self._blocks(added) if added else
                   f"No new evidence ({info['results']} result(s), all already provided).")
        return ToolResult(True, f"Searched for '{query}' ({len(added)} new result(s))", content,
                          added, lines)

    def _read_file(self, args: dict) -> ToolResult:
        _only(args, {"path", "start_line", "end_line"})
        record = self._manifest_path(_text(args, "path", max_len=1024))
        start = _int(args, "start_line")
        end = _int(args, "end_line")
        if end < start:
            raise ToolArgumentError("'end_line' must not be before 'start_line'.")
        if start > record.line_count:
            raise ToolArgumentError(f"'{record.path}' has only {record.line_count} lines.")
        end = min(end, record.line_count, start + MAX_READ_LINES - 1)
        item, created, lines = self._add(record.path, start, end, method="read_file",
                                         provenance="read_file")
        if item is None:
            return ToolResult(False, "Read limit reached", "Error: line or evidence limit reached.")
        note = "" if created else f"(already provided as {item.id})\n"
        return ToolResult(True, f"Read {record.path}:{item.start_line}-{item.end_line}",
                          note + self._blocks([item]), [item] if created else [], lines)

    def _find_symbol(self, args: dict) -> ToolResult:
        _only(args, {"name", "path"})
        name = _text(args, "name", max_len=255).strip("`() ")
        path = _text(args, "path", required=False, max_len=1024)
        query = select(CodeSymbol).where(
            CodeSymbol.project_id == self.project_id,
            or_(CodeSymbol.name == name, CodeSymbol.qualified_name == name,
                CodeSymbol.qualified_name.like("%." + _like(name), escape="\\")),
        )
        if path:
            query = query.where(CodeSymbol.path == self._manifest_path(path).path)
        symbols = self.req.session.scalars(
            query.order_by(func.length(CodeSymbol.qualified_name), CodeSymbol.path)
            .limit(MAX_SYMBOL_MATCHES)
        ).all()
        if not symbols:
            return ToolResult(True, f"Looked up `{name}` (not found)",
                              f"No symbol named '{name}' was found in this project.")
        added: list[Evidence] = []
        shown: list[Evidence] = []
        lines = 0
        for s in symbols:
            end = min(s.end_line, s.start_line + MAX_SYMBOL_LINES - 1)
            item, created, n = self._add(s.path, s.start_line, end, method="find_symbol",
                                         provenance=self._why(f"find_symbol: {name}"),
                                         symbol=s.qualified_name, symbol_kind=s.kind)
            if item is None:
                break
            lines += n
            shown.append(item)
            if created:
                added.append(item)
        if not shown:
            return ToolResult(False, "Read limit reached", "Error: line or evidence limit reached.")
        refs = ", ".join(f"{s.qualified_name} ({s.path}:{s.start_line})" for s in symbols)
        return ToolResult(True, f"Inspected `{name}`", f"Matches: {refs}\n\n" +
                          self._blocks(shown), added, lines)

    def _get_chunk(self, args: dict) -> ToolResult:
        _only(args, {"chunk_id"})
        chunk_id = _text(args, "chunk_id", max_len=64)
        entry = self.req.session.scalar(
            select(IndexEntry).where(IndexEntry.project_id == self.project_id,
                                     IndexEntry.record_id == chunk_id)
        )
        if entry is None:
            raise ToolArgumentError("No chunk with that id exists in this project.")
        item, created, lines = self._add(entry.path, entry.start_line, entry.end_line,
                                         method="get_chunk", provenance="get_chunk",
                                         symbol=entry.symbol, symbol_kind=entry.symbol_kind,
                                         chunk_id=entry.record_id,
                                         expected_hash=entry.content_hash)
        if item is None:
            return ToolResult(False, "Read limit reached", "Error: line or evidence limit reached.")
        return ToolResult(True, f"Read chunk {entry.path}:{entry.start_line}-{entry.end_line}",
                          self._blocks([item]), [item] if created else [], lines)

    def _list_project_files(self, args: dict) -> ToolResult:
        _only(args, {"directory"})
        directory = _text(args, "directory", required=False, max_len=1024)
        query = select(ProjectFile.path, ProjectFile.language, ProjectFile.line_count).where(
            ProjectFile.project_id == self.project_id)
        if directory:
            directory = directory.strip("/")
            query = query.where(ProjectFile.path.like(_like(directory) + "/%", escape="\\"))
        rows = self.req.session.execute(query.order_by(ProjectFile.path)
                                        .limit(MAX_FILE_LIST + 1)).all()
        more = len(rows) > MAX_FILE_LIST
        listing = "\n".join(f"{p} ({lang}, {n} lines)" for p, lang, n in rows[:MAX_FILE_LIST])
        return ToolResult(True, f"Listed files{' in ' + directory if directory else ''} "
                          f"({min(len(rows), MAX_FILE_LIST)})",
                          (listing or "No files.") + ("\n(list truncated)" if more else ""))

    def _get_project_overview(self, args: dict) -> ToolResult:
        _only(args, set())
        session = self.req.session
        langs = session.execute(
            select(ProjectFile.language, func.count(), func.sum(ProjectFile.line_count))
            .where(ProjectFile.project_id == self.project_id).group_by(ProjectFile.language)
            .order_by(func.count().desc())
        ).all()
        tops: dict[str, int] = {}
        for (path,) in session.execute(select(ProjectFile.path).where(
                ProjectFile.project_id == self.project_id)).all():
            top = path.split("/")[0] + ("/" if "/" in path else "")
            tops[top] = tops.get(top, 0) + 1
        kinds = session.execute(
            select(CodeSymbol.kind, func.count()).where(CodeSymbol.project_id == self.project_id)
            .group_by(CodeSymbol.kind)
        ).all()
        content = (
            f"Project: {self.req.project.name}\n"
            "Languages: " + ", ".join(f"{lang} ({n} files, {lines} lines)"
                                      for lang, n, lines in langs) + "\n"
            "Top level: " + ", ".join(f"{k} ({v})" for k, v in sorted(tops.items())[:40]) + "\n"
            "Symbols: " + (", ".join(f"{k} {v}" for k, v in sorted(kinds)) or "none")
        )
        return ToolResult(True, "Read project overview", content)


def _key(args: Any) -> str:
    if isinstance(args, dict):
        return repr(sorted((str(k), _key(v)) for k, v in args.items()))
    if isinstance(args, str):
        return " ".join(args.lower().split())
    return repr(args)
