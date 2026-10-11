"""Citation validation and a separate, deterministic support check.

Validity: a cited ID was issued for this answer, belongs to this project, its file is still a
manifest file of the project, the range exists and the lines still hash to what the model saw.
Invalid references are removed from the answer text and reported; they are never rendered as
genuine. Validity does not prove support; ``assess_support`` estimates that separately.
"""
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import ProjectFile
from app.services.chunking import split_lines
from app.services.ingestion import IngestionError, read_project_file
from app.services.rag.evidence import Evidence, lines_hash

# [E1], [E1, E2], [E1][E2]; also catches IDs the model made up, such as [E42].
CITATION_GROUP = re.compile(r"\[\s*(E\d+(?:\s*[,;]\s*E\d+)*)\s*\]")
EVIDENCE_ID = re.compile(r"E\d+")


@dataclass
class CitationIssue:
    evidence_id: str
    reason: str


@dataclass
class CitationReport:
    text: str  # answer with invalid references removed
    valid: list[Evidence] = field(default_factory=list)  # in first-citation order
    invalid: list[CitationIssue] = field(default_factory=list)
    cited_count: int = 0  # citation occurrences in the original answer

    @property
    def warnings(self) -> list[str]:
        return [f"Removed citation {i.evidence_id}: {i.reason}" for i in self.invalid]


def cited_ids(text: str) -> list[str]:
    ids: list[str] = []
    for group in CITATION_GROUP.findall(text):
        ids.extend(EVIDENCE_ID.findall(group))
    return ids


def _check(
    session: Session, settings: Settings, project_id: str, evidence: Evidence | None,
    lines_cache: dict[str, list[str] | None],
) -> str | None:
    """Reason the citation is invalid, or None when it checks out."""
    if evidence is None:
        return "this evidence ID was not provided to the model"
    if evidence.project_id != project_id:
        return "the evidence belongs to another project"
    record = session.scalar(
        select(ProjectFile).where(
            ProjectFile.project_id == project_id, ProjectFile.path == evidence.path
        )
    )
    if record is None or record.id != evidence.file_id:
        return f"'{evidence.path}' is no longer a file of this project"
    if evidence.path not in lines_cache:
        try:
            lines_cache[evidence.path] = split_lines(
                read_project_file(settings, project_id, evidence.path)
            )
        except (IngestionError, OSError):
            lines_cache[evidence.path] = None
    lines = lines_cache[evidence.path]
    if lines is None:
        return f"'{evidence.path}' could not be read"
    if evidence.start_line < 1 or evidence.end_line < evidence.start_line or \
            evidence.end_line > len(lines):
        return f"lines {evidence.start_line}-{evidence.end_line} do not exist in '{evidence.path}'"
    current = "".join(lines[evidence.start_line - 1:evidence.end_line])
    if lines_hash(current) != evidence.content_hash:
        return f"'{evidence.path}' changed after the evidence was read (stale source)"
    return None


def validate_citations(
    session: Session,
    settings: Settings,
    project_id: str,
    text: str,
    provided: dict[str, Evidence],
) -> CitationReport:
    """``provided`` holds exactly the evidence shown to the model, by ID."""
    report = CitationReport(text=text)
    verdicts: dict[str, str | None] = {}
    lines_cache: dict[str, list[str] | None] = {}
    for eid in cited_ids(text):
        report.cited_count += 1
        if eid not in verdicts:
            verdicts[eid] = _check(session, settings, project_id, provided.get(eid), lines_cache)
            if verdicts[eid] is None:
                report.valid.append(provided[eid])
            else:
                report.invalid.append(CitationIssue(eid, verdicts[eid]))

    def rewrite(match: re.Match) -> str:
        keep = [i for i in EVIDENCE_ID.findall(match.group(1)) if verdicts.get(i) is None]
        return "".join(f"[{i}]" for i in keep)

    if report.invalid:
        report.text = CITATION_GROUP.sub(rewrite, text)
    return report


# -- support (deterministic, lexical) ---------------------------------------------------------

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
CODE_SPAN = re.compile(r"`([^`\n]{1,120})`")
IDENTIFIER = re.compile(r"\b(?:[A-Za-z_][A-Za-z0-9_]*\.)*[A-Za-z_][A-Za-z0-9_]*\b")


def _code_terms(sentence: str) -> set[str]:
    """Code names a sentence asserts: backticked spans, plus snake_case/camelCase/dotted names."""
    terms: set[str] = set()
    for span in CODE_SPAN.findall(sentence):
        span = span.strip().rstrip("()")
        if span:
            terms.add(span)
    for token in IDENTIFIER.findall(CODE_SPAN.sub(" ", sentence)):
        if "_" in token.strip("_") or "." in token or re.search(r"[a-z][A-Z]", token):
            terms.add(token)
    return {t for t in terms if not EVIDENCE_ID.fullmatch(t)}


def _term_supported(term: str, haystack: str, paths: set[str]) -> bool:
    if term in haystack or term in paths:
        return True
    # `AuthService.authenticate` is supported if each dotted part appears.
    parts = [p for p in re.split(r"[.:/]", term) if p]
    return bool(parts) and all(p in haystack for p in parts)


@dataclass
class SupportReport:
    claims: int = 0  # sentences that assert code names
    cited_claims: int = 0
    supported_claims: int = 0  # cited, and every code name appears in the cited evidence
    unsupported: list[str] = field(default_factory=list)  # sentence excerpts
    uncited: list[str] = field(default_factory=list)

    @property
    def unsupported_rate(self) -> float | None:
        if not self.claims:
            return None
        return round((self.claims - self.supported_claims) / self.claims, 3)


def assess_support(text: str, provided: dict[str, Evidence]) -> SupportReport:
    """Lexical check of whether code names in each cited sentence occur in the cited lines.

    It cannot judge meaning: a sentence can name the right identifiers and still be wrong. It is
    reported as an estimate and never used to mark an answer as correct."""
    report = SupportReport()
    for sentence in SENTENCE_SPLIT.split(text):
        terms = _code_terms(sentence)
        if not terms:
            continue
        report.claims += 1
        ids = [i for i in cited_ids(sentence) if i in provided]
        excerpt = sentence.strip()[:160]
        if not ids:
            report.uncited.append(excerpt)
            continue
        report.cited_claims += 1
        haystack = "\n".join(provided[i].text for i in ids)
        paths = {provided[i].path for i in ids}
        if all(_term_supported(t, haystack, paths) for t in terms):
            report.supported_claims += 1
        else:
            report.unsupported.append(excerpt)
    return report
