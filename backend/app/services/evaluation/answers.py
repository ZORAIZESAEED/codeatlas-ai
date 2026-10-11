"""Answer-quality benchmark (Milestone 4): baseline RAG vs. bounded investigation.

Imports the fixture repositories into a temporary database, workspace and vector store (never
the development data), runs the real analysis/indexing pipeline, then asks every labelled
question of ``benchmarks/answers.json`` in each answer mode with the configured chat model.

All checks are deterministic (see answers.json). No LLM judge is used, so "correctness" here
means: the answer cites the labelled code, names the labelled identifiers, avoids forbidden
strings and refuses when the repository has no answer. That is a proxy, reported as such.

    cd backend
    python -m app.services.evaluation.answers --llm-model qwen2.5:3b            # real model
    python -m app.services.evaluation.answers --llm-provider fake --embedding-provider fake \
        --no-write                                                              # harness check
"""
import argparse
import json
import platform
import re
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.db.models import CodeSymbol, Project
from app.db.session import create_db_engine
from app.services.analysis import run_analysis
from app.services.evaluation.benchmark import BENCHMARK_DIR, _migrate, _zip_directory
from app.services.indexing.indexer import index_project
from app.services.ingestion import import_archive
from app.services.investigation.controller import run_investigation
from app.services.llm.base import LLMError
from app.services.llm.registry import create_provider
from app.services.rag.answer import AnswerRequest, ChatError, HistoryTurn, run_baseline

MODES = ("baseline", "investigation")
REFUSAL = re.compile(
    r"(does not|doesn't|do not|don't|cannot|can't|could not|couldn't|no|not)\b[^.]{0,80}"
    r"\b(show|shows|contain|contains|include|includes|find|found|evidence|mention|mentions|"
    r"indicate|indicates|appear|appears|information|implement|implemented|define|defined)\b",
    re.IGNORECASE,
)


def _symbol_ranges(session, project_id: str, path: str, symbol: str) -> list[tuple[int, int]]:
    rows = session.execute(
        select(CodeSymbol.start_line, CodeSymbol.end_line).where(
            CodeSymbol.project_id == project_id, CodeSymbol.path == path,
            CodeSymbol.qualified_name == symbol,
        )
    ).all()
    return [(a, b) for a, b in rows]


def resolve_labels(session, projects: dict[str, str], spec: dict) -> dict[str, list]:
    """Each must_cite alternative -> (path, line ranges or None). A label naming a symbol that
    does not exist aborts the run: that is a broken benchmark, not a model failure."""
    resolved: dict[str, list] = {}
    for q in spec["questions"]:
        pid = projects[q["repo"]]
        groups = []
        for group in q.get("must_cite", []):
            alternatives = []
            for label in group:
                ranges = None
                if label.get("symbol"):
                    ranges = _symbol_ranges(session, pid, label["path"], label["symbol"])
                    if not ranges:
                        raise ValueError(f"Label {label} of {q['id']} matches no symbol.")
                alternatives.append((label["path"], ranges))
            groups.append(alternatives)
        resolved[q["id"]] = groups
    return resolved


def _cites(citations, path: str, ranges) -> bool:
    for c in citations:
        if c.path != path:
            continue
        if ranges is None or any(c.start_line <= e and s <= c.end_line for s, e in ranges):
            return True
    return False


def score(question: dict, groups: list, result) -> dict[str, Any]:
    text = result.text
    checks: dict[str, Any] = {}
    if groups:
        hits = [any(_cites(result.citations, p, r) for p, r in g) for g in groups]
        checks["citation_recall"] = round(sum(hits) / len(hits), 3)
    mention_groups = question.get("must_mention", [])
    if mention_groups:
        hits = [any(term in text for term in g) for g in mention_groups]
        checks["mention_recall"] = round(sum(hits) / len(hits), 3)
    forbidden = [s for s in question.get("must_not_mention", []) if s.lower() in text.lower()]
    checks["forbidden_found"] = forbidden
    if question.get("expect_refusal"):
        checks["refused"] = bool(REFUSAL.search(text)) or result.finish_reason == "no_evidence"
    valid, invalid = len(result.citations), len(result.invalid_citations)
    checks["citation_validity"] = round(valid / (valid + invalid), 3) if valid + invalid else None
    checks["unsupported_claim_rate"] = result.support.unsupported_rate
    checks["claims"] = result.support.claims
    # Relationship verdicts are reported, not part of "passed", so runs stay comparable.
    verdicts = [r["verdict"] for r in getattr(result, "relationships", [])]
    checks["relationships"] = {v: verdicts.count(v) for v in sorted(set(verdicts))}
    passed = (
        checks.get("citation_recall", 1.0) == 1.0
        and checks.get("mention_recall", 1.0) == 1.0
        and not forbidden
        and checks.get("refused", True)
    )
    checks["passed"] = passed
    return checks


def _mean(values) -> float | None:
    values = [v for v in values if v is not None]
    return round(mean(values), 3) if values else None


def summarize(rows: list[dict]) -> dict[str, Any]:
    ok = [r for r in rows if r["status"] == "completed"]
    return {
        "questions": len(rows),
        "completed": len(ok),
        "errors": len(rows) - len(ok),
        "pass_rate": _mean([1.0 if r["checks"]["passed"] else 0.0 for r in ok]),
        "citation_recall": _mean([r["checks"].get("citation_recall") for r in ok]),
        "mention_recall": _mean([r["checks"].get("mention_recall") for r in ok]),
        "citation_validity": _mean([r["checks"].get("citation_validity") for r in ok]),
        "unsupported_claim_rate": _mean([r["checks"].get("unsupported_claim_rate") for r in ok]),
        "refusal_accuracy": _mean([1.0 if r["checks"]["refused"] else 0.0 for r in ok
                                   if "refused" in r["checks"]]),
        "forbidden_violations": sum(len(r["checks"]["forbidden_found"]) for r in ok),
        "relationship_claims": {
            v: sum(r["checks"].get("relationships", {}).get(v, 0) for r in ok)
            for v in ("verified", "inferred", "unverified", "unsupported")
        },
        "mean_tool_calls": _mean([r["tool_calls"] for r in ok]),
        "mean_llm_requests": _mean([r["llm_requests"] for r in ok]),
        "median_latency_s": round(median([r["latency_s"] for r in ok]), 1) if ok else None,
        "input_tokens": sum(r["input_tokens"] or 0 for r in ok) or None,
        "output_tokens": sum(r["output_tokens"] or 0 for r in ok) or None,
    }


def run_answers(
    settings: Settings,
    modes: tuple[str, ...] = MODES,
    spec_path: Path = BENCHMARK_DIR / "answers.json",
    only: set[str] | None = None,
    workdir: Path | None = None,
    provider_factory=None,
    progress=print,
) -> dict:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    questions = [q for q in spec["questions"] if not only or q["id"] in only
                 or any(q["id"] == o.get("follow_up_of") for o in spec["questions"]
                        if o["id"] in (only or ()))]
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(workdir or tmp)
        database_url = f"sqlite:///{(root / 'answers.db').as_posix()}"
        _migrate(database_url)
        settings = settings.model_copy(update={
            "database_url": database_url, "workspace_dir": str(root / "workspaces"),
            "chroma_dir": str(root / "chroma"),
        })
        engine = create_db_engine(database_url)
        session = sessionmaker(bind=engine, expire_on_commit=False)()
        try:
            projects = {}
            for repo in {q["repo"] for q in questions}:
                project = import_archive(session, settings,
                                         _zip_directory(spec_path.parent
                                                        / spec["repositories"][repo]),
                                         name=repo, source_type="zip")
                run_analysis(session, settings, project)
                state = index_project(session, settings, project.id)
                if state.status != "ready":
                    raise RuntimeError(f"Indexing {repo} failed: {state.error}")
                projects[repo] = project.id
            labels = resolve_labels(session, projects, {"questions": questions})
            results: dict[str, Any] = {"modes": {}, "questions": {}}
            factory = provider_factory or create_provider
            for mode in modes:
                rows = []
                history: dict[str, HistoryTurn] = {}
                for q in questions:
                    provider = factory(settings)
                    prior = q.get("follow_up_of")
                    req = AnswerRequest(
                        session=session, settings=settings, provider=provider,
                        project=session.get(Project, projects[q["repo"]]), question=q["question"],
                        history=[history[prior]] if prior in history else [],
                    )
                    started = time.perf_counter()
                    row: dict[str, Any] = {"id": q["id"], "category": q["category"],
                                           "mode": mode}
                    try:
                        if mode == "baseline":
                            result = run_baseline(req)
                        else:
                            result, planner = run_investigation(req, [])
                            row["planner"] = planner
                        row.update(
                            status="completed", answer=result.text,
                            citations=[f"{c.id} {c.reference}" for c in result.citations],
                            invalid_citations=[f"{i.evidence_id}: {i.reason}"
                                               for i in result.invalid_citations],
                            tool_calls=result.tool_calls, llm_requests=result.llm_requests,
                            input_tokens=result.usage.input_tokens,
                            output_tokens=result.usage.output_tokens,
                            trace_tools=[t.get("summary") for t in result.trace
                                         if t.get("tool") and t.get("step")],
                            checks=score(q, labels[q["id"]], result),
                        )
                        history[q["id"]] = HistoryTurn(q["question"], result.text)
                    except (LLMError, ChatError) as exc:
                        session.rollback()
                        row.update(status="error", error=str(exc), checks={})
                    finally:
                        provider.close()
                    row["latency_s"] = round(time.perf_counter() - started, 1)
                    rows.append(row)
                    results["questions"].setdefault(q["id"], {})[mode] = row
                    progress(f"[{mode}] {q['id']}: {row['status']} "
                             f"{'PASS' if row.get('checks', {}).get('passed') else 'fail'} "
                             f"{row['latency_s']} s")
                by_category: dict[str, list] = defaultdict(list)
                for r in rows:
                    by_category[r["category"]].append(r)
                results["modes"][mode] = {
                    "overall": summarize(rows),
                    "by_category": {c: summarize(rs) for c, rs in sorted(by_category.items())},
                }
            return results
        finally:
            session.close()
            engine.dispose()


def cost(settings: Settings, summary: dict) -> float | None:
    if settings.llm_input_cost_per_mtok is None or settings.llm_output_cost_per_mtok is None:
        return None
    if summary["input_tokens"] is None or summary["output_tokens"] is None:
        return None
    return round(summary["input_tokens"] / 1e6 * settings.llm_input_cost_per_mtok
                 + summary["output_tokens"] / 1e6 * settings.llm_output_cost_per_mtok, 4)


def to_markdown(report: dict) -> str:
    meta = report["meta"]
    lines = [f"# Answer benchmark - {meta['date']}", "",
             f"Chat model: `{meta['llm_provider']}/{meta['llm_model']}` · embeddings: "
             f"`{meta['embedding_model']}` · context {meta['context_tokens']} tokens · "
             f"max output {meta['output_tokens']} · {meta['questions']} questions",
             f"Machine: {meta['machine']}", "",
             "Deterministic checks only (no LLM judge). See benchmarks/answers.json.", "",
             "| Mode | Pass | Citation recall | Mention recall | Citation validity | "
             "Unsupported claims | Refusals | Forbidden | Tools/q | LLM req/q | Median s | "
             "Errors |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for mode, data in report["results"]["modes"].items():
        o = data["overall"]
        lines.append(
            f"| {mode} | {o['pass_rate']} | {o['citation_recall']} | {o['mention_recall']} | "
            f"{o['citation_validity']} | {o['unsupported_claim_rate']} | "
            f"{o['refusal_accuracy']} | {o['forbidden_violations']} | {o['mean_tool_calls']} | "
            f"{o['mean_llm_requests']} | {o['median_latency_s']} | {o['errors']} |")
    lines += ["", "## Per question", "",
              "| Question | Category | " + " | ".join(report["results"]["modes"]) + " |",
              "| --- | --- | " + " | ".join("---" for _ in report["results"]["modes"]) + " |"]
    for qid, by_mode in report["results"]["questions"].items():
        cells = []
        for mode in report["results"]["modes"]:
            r = by_mode.get(mode, {})
            if r.get("status") != "completed":
                cells.append(f"error: {str(r.get('error', '?'))[:60]}")
                continue
            c = r["checks"]
            cells.append(("PASS" if c["passed"] else "fail")
                         + f" (cite {c.get('citation_recall', '-')}, "
                           f"mention {c.get('mention_recall', '-')}"
                         + (f", refused {c['refused']}" if "refused" in c else "")
                         + (f", forbidden {c['forbidden_found']}" if c["forbidden_found"] else "")
                         + f", {r['latency_s']} s)")
        category = next(iter(by_mode.values()))["category"]
        lines.append(f"| {qid} | {category} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--llm-provider", default=None)
    parser.add_argument("--llm-model", default=None)
    parser.add_argument("--embedding-provider", default=None)
    parser.add_argument("--embedding-model", default=None)
    parser.add_argument("--modes", default=",".join(MODES))
    parser.add_argument("--only", default="", help="comma-separated question ids")
    parser.add_argument("--context-tokens", type=int, default=None)
    parser.add_argument("--output-tokens", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    base = Settings()
    updates = {k: v for k, v in {
        "llm_provider": args.llm_provider, "llm_model": args.llm_model,
        "embedding_provider": args.embedding_provider, "embedding_model": args.embedding_model,
        "llm_max_context_tokens": args.context_tokens,
        "llm_max_output_tokens": args.output_tokens,
        "llm_request_timeout_seconds": args.timeout,
    }.items() if v is not None}
    settings = base.model_copy(update=updates)
    started = time.perf_counter()
    results = run_answers(settings, tuple(m for m in args.modes.split(",") if m),
                          only={i for i in args.only.split(",") if i} or None)
    report = {
        "meta": {
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "llm_provider": settings.llm_provider, "llm_model": settings.llm_model,
            "embedding_model": settings.embedding_model,
            "context_tokens": settings.llm_max_context_tokens,
            "output_tokens": settings.llm_max_output_tokens,
            "questions": len(results["questions"]),
            "machine": f"{platform.processor() or platform.machine()}, {platform.system()}",
            "wall_seconds": round(time.perf_counter() - started, 1),
            "estimated_cost_usd": {m: cost(settings, d["overall"])
                                   for m, d in results["modes"].items()},
        },
        "results": results,
    }
    markdown = to_markdown(report)
    print(markdown)
    if not args.no_write:
        out = BENCHMARK_DIR / "results"
        slug = re.sub(r"[^a-z0-9]+", "-", f"{settings.llm_provider}-{settings.llm_model}".lower())
        stem = f"{report['meta']['date']}_answers_{slug.strip('-')}"
        (out / f"{stem}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (out / f"{stem}.md").write_text(markdown, encoding="utf-8")
        print(f"Wrote {out / stem}.json/.md")


if __name__ == "__main__":
    main()
