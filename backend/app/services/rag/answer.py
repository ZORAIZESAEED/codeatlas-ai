"""Source-grounded answers: retrieval -> evidence -> bounded context -> model -> citation check.

``run_baseline`` is the single-pass pipeline. The investigation controller gathers more evidence
with read-only tools and then calls ``generate_answer`` with the same context assembly and
citation validation, so both modes are measured on identical infrastructure.
"""
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import Project
from app.services.llm.base import (
    CancelToken,
    LLMError,
    LLMProvider,
    Message,
    ToolSpec,
    Usage,
)
from app.services.llm.base import Generation as ModelGeneration
from app.services.rag.citations import (
    CitationIssue,
    SupportReport,
    assess_support,
    validate_citations,
)
from app.services.rag.context import assemble_context, estimate_tokens
from app.services.rag.evidence import Evidence, EvidenceError, EvidenceSet
from app.services.rag.prompts import SYSTEM_PROMPT
from app.services.rag.relations import cited_paths_by_sentence, verify_relationships
from app.services.retrieval.hybrid import search
from app.services.retrieval.models import SearchError, SearchFilters

Emit = Callable[[str, dict[str, Any]], None]

NO_EVIDENCE_ANSWER = (
    "I could not find source evidence for this in the indexed repository, so I can't answer it "
    "from the code. Try naming a file, function or feature, or re-index the project if it "
    "changed."
)


class ChatError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass
class HistoryTurn:
    question: str
    answer: str


@dataclass
class AnswerRequest:
    session: Session
    settings: Settings
    provider: LLMProvider
    project: Project
    question: str
    history: list[HistoryTurn] = field(default_factory=list)
    retrieval_mode: str | None = None
    file_paths: list[str] = field(default_factory=list)
    history_notes: list[str] = field(default_factory=list)
    cancel: CancelToken = field(default_factory=CancelToken)
    emit: Emit = lambda event, data: None  # noqa: E731


@dataclass
class ModelCalls:
    """Counts and limits model requests; sums the usage providers actually reported."""

    max_requests: int
    requests: int = 0
    usage: Usage = field(default_factory=Usage)

    def stream(self, req: AnswerRequest, messages: list[Message], *, system: str,
               tools: list[ToolSpec] | None = None, on_text: Callable[[str], None] | None = None,
               max_tokens: int | None = None) -> ModelGeneration:
        if self.requests >= self.max_requests:
            raise ChatError("limit_reached", "The model request limit was reached.", 429)
        self.requests += 1
        settings = req.settings
        final: ModelGeneration | None = None
        for delta in req.provider.stream(
            messages, system=system, tools=tools,
            max_tokens=max_tokens or settings.llm_max_output_tokens,
            temperature=settings.llm_temperature, cancel=req.cancel,
        ):
            if delta.text and on_text is not None:
                on_text(delta.text)
            if delta.done:
                final = delta.generation
        if final is None:
            raise LLMError("invalid_response", "The model response ended without completing.")
        self.usage = self.usage.add(final.usage)
        return final


@dataclass
class AnswerResult:
    text: str
    raw_text: str
    mode: str
    citations: list[Evidence]
    invalid_citations: list[CitationIssue]
    evidence: list[Evidence]  # evidence actually shown to the model
    support: SupportReport
    warnings: list[str]
    usage: Usage
    provider: str
    model: str
    llm_requests: int
    tool_calls: int = 0
    trace: list[dict[str, Any]] = field(default_factory=list)
    budget: dict[str, int] = field(default_factory=dict)
    timings_ms: dict[str, int] = field(default_factory=dict)
    finish_reason: str | None = None
    # Static checks of explicit relationship claims (see rag/relations.py).
    relationships: list[dict[str, Any]] = field(default_factory=list)


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def history_summary(turns: list[HistoryTurn], max_tokens: int) -> str:
    """Recent turns, newest kept first when the budget is tight. Assistant text is shortened and
    stripped of citation IDs, because earlier evidence IDs mean nothing in a new request."""
    parts: list[str] = []
    used = 0
    for turn in reversed(turns):
        answer = re.sub(r"\[\s*E\d+(?:\s*[,;]\s*E\d+)*\s*\]", "", turn.answer)
        answer = " ".join(answer.split())[:500]
        part = f"User asked: {turn.question.strip()[:300]}\nAssistant replied: {answer}"
        cost = estimate_tokens(part)
        if used + cost > max_tokens:
            break
        parts.append(part)
        used += cost
    return "\n\n".join(reversed(parts))


def retrieval_queries(req: AnswerRequest) -> list[str]:
    """The question itself; for follow-ups also the previous question joined with it, so
    "where is it validated?" keeps the earlier topic."""
    queries = [req.question]
    if req.history:
        queries.append(f"{req.history[-1].question} {req.question}")
    return queries


def search_evidence(
    req: AnswerRequest, evidence: EvidenceSet, query: str, *, limit: int,
    filters: SearchFilters | None = None, provenance: str | None = None,
) -> tuple[list[Evidence], dict[str, Any]]:
    """Run the existing retrieval pipeline and turn validated hits into evidence."""
    settings = req.settings
    try:
        outcome = search(req.session, settings, req.project.id, query,
                         mode=req.retrieval_mode or settings.rag_search_mode, limit=limit,
                         filters=filters)
    except SearchError as exc:
        if exc.status_code == 409:
            raise ChatError("index_missing", exc.message, 409) from exc
        if exc.status_code == 503:
            raise ChatError("retrieval_unavailable", exc.message, 503) from exc
        raise ChatError("retrieval_failed", exc.message, exc.status_code) from exc
    added: list[Evidence] = []
    for candidate in outcome.results:
        try:
            item, created = evidence.add_candidate(candidate, provenance or f"search: {query}")
        except EvidenceError:
            continue  # source changed between validation and read; skip it
        if created:
            added.append(item)
    info = {"query": query, "mode_used": outcome.mode_used, "results": len(outcome.results),
            "new_evidence": len(added), "warnings": outcome.warnings,
            "duration_ms": int(outcome.timings_ms.get("total", 0))}
    return added, info


def initial_retrieval(req: AnswerRequest, evidence: EvidenceSet,
                      limit: int | None = None) -> list[str]:
    """Retrieve evidence for the question (and selected files). Returns notes for the prompt."""
    settings = req.settings
    limit = limit or settings.rag_search_limit
    notes: list[str] = []
    req.emit("retrieval_started", {"queries": retrieval_queries(req)})
    t = time.perf_counter()
    infos = []
    for path in req.file_paths:
        if evidence.file_record(path) is None:
            raise ChatError("invalid_file", f"'{path}' is not a file of this project.", 422)
        _, info = search_evidence(req, evidence, req.question, limit=3,
                                  filters=SearchFilters(file_path=path),
                                  provenance=f"selected file: {path}")
        infos.append(info)
    for query in retrieval_queries(req):
        _, info = search_evidence(req, evidence, query, limit=limit)
        infos.append(info)
    warnings = sorted({w for info in infos for w in info["warnings"]})
    notes.extend(warnings)
    req.emit("retrieval_completed", {
        "evidence_count": len(evidence), "searches": len(infos),
        "mode_used": infos[-1]["mode_used"] if infos else None,
        "warnings": warnings, "duration_ms": _ms(t),
    })
    return notes


def generate_answer(
    req: AnswerRequest,
    evidence: list[Evidence],
    notes: list[str],
    calls: ModelCalls,
    *,
    mode: str,
) -> AnswerResult:
    settings = req.settings
    timings: dict[str, int] = {}
    warnings = [n for n in notes]
    if not evidence:
        req.emit("answer_delta", {"text": NO_EVIDENCE_ANSWER})
        return AnswerResult(
            text=NO_EVIDENCE_ANSWER, raw_text=NO_EVIDENCE_ANSWER, mode=mode, citations=[],
            invalid_citations=[], evidence=[], support=SupportReport(), warnings=warnings,
            usage=calls.usage, provider=req.provider.name, model=req.provider.model,
            llm_requests=calls.requests, finish_reason="no_evidence",
        )
    context_tokens = settings.llm_max_context_tokens
    context = assemble_context(
        system=SYSTEM_PROMPT, question=req.question, evidence=evidence[: _max_evidence(req, mode)],
        history_summary=history_summary(req.history, settings.chat_history_max_tokens),
        notes=notes + req.history_notes, context_tokens=context_tokens,
        output_tokens=settings.llm_max_output_tokens,
    )
    if context.truncated:
        warnings.append(f"{len(context.truncated)} evidence block(s) were shortened to fit the "
                        "context budget.")
    if context.dropped:
        warnings.append(f"{len(context.dropped)} evidence block(s) did not fit the context "
                        "budget and were not sent to the model.")
    req.emit("generation_started", {"evidence_count": len(context.included),
                                    "estimated_prompt_tokens":
                                        context.budget["estimated_prompt_tokens"]})
    t = time.perf_counter()
    generation = calls.stream(
        req, [Message("user", context.user_message)], system=SYSTEM_PROMPT,
        max_tokens=context.budget["reserved_output_tokens"],
        on_text=lambda piece: req.emit("answer_delta", {"text": piece}),
    )
    timings["generation"] = _ms(t)
    req.cancel.raise_if_cancelled()
    raw = generation.text.strip()
    if not raw:
        raise LLMError("invalid_response", "The model returned an empty answer.")
    if generation.finish_reason == "length":
        warnings.append("The answer reached the output token limit and may be incomplete.")
    provided = {e.id: e for e in context.included}
    t = time.perf_counter()
    report = validate_citations(req.session, settings, req.project.id, raw, provided)
    support = assess_support(report.text, provided)
    timings["citation_validation"] = _ms(t)
    warnings.extend(report.warnings)
    if not report.valid and support.claims:
        # A refusal that names no code needs no citation; code claims without one get flagged.
        warnings.append("The answer cites no verified source evidence.")
    # Explicit "A calls/imports/returns B" claims are checked against the parsed source; claims
    # that are not verified are labelled in the text, never silently kept as fact or deleted.
    t = time.perf_counter()
    relations = verify_relationships(
        req.session, settings, req.project.id, report.text,
        cited_paths_by_sentence(report.text, provided),
    )
    timings["relationship_check"] = _ms(t)
    warnings.extend(relations.warnings)
    return AnswerResult(
        text=relations.text, raw_text=raw, mode=mode, citations=report.valid,
        relationships=[c.as_dict() for c in relations.checks],
        invalid_citations=report.invalid, evidence=context.included, support=support,
        warnings=warnings, usage=calls.usage, provider=generation.provider,
        model=generation.model, llm_requests=calls.requests, budget=context.budget,
        timings_ms=timings, finish_reason=generation.finish_reason,
    )


def _max_evidence(req: AnswerRequest, mode: str) -> int:
    s = req.settings
    return s.investigation_max_evidence if mode == "investigation" else s.rag_max_evidence


def run_baseline(req: AnswerRequest) -> AnswerResult:
    started = time.perf_counter()
    evidence = EvidenceSet(req.session, req.settings, req.project)
    notes = initial_retrieval(req, evidence)
    req.cancel.raise_if_cancelled()
    calls = ModelCalls(max_requests=1)
    result = generate_answer(req, evidence.items, notes, calls, mode="baseline")
    result.trace.insert(0, {"step": "retrieval", "queries": retrieval_queries(req),
                            "evidence": [e.reference for e in evidence.items]})
    result.timings_ms["total"] = _ms(started)
    return result
