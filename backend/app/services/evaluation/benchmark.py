"""Reproducible retrieval benchmark.

Imports the controlled repositories in ``backend/benchmarks/repos`` into a temporary database,
workspace and vector store (never the development data), runs the real analysis and indexing
pipeline, then runs every labelled query of ``benchmarks/queries.json`` under each retrieval
configuration with identical labels.

    cd backend
    python -m app.services.evaluation.benchmark                    # local model from .env
    python -m app.services.evaluation.benchmark --provider fake --model fake-hash-64 --no-write
"""
import argparse
import io
import json
import os
import platform
import re
import tempfile
import time
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median

from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, get_settings
from app.db.models import IndexEntry, SearchIndex
from app.db.session import create_db_engine
from app.services.analysis import run_analysis
from app.services.chunking import split_lines
from app.services.evaluation.metrics import Ranking, summarize
from app.services.indexing.documents import EMBEDDING_INPUT_VERSION
from app.services.indexing.indexer import index_project
from app.services.ingestion import import_archive, read_project_file
from app.services.retrieval import reranker as reranker_module
from app.services.retrieval.hybrid import search

BACKEND_DIR = Path(__file__).resolve().parents[3]
BENCHMARK_DIR = BACKEND_DIR / "benchmarks"
CONFIGS = (
    ("keyword", "keyword", False),
    ("semantic", "semantic", False),
    ("hybrid", "hybrid", False),
    ("hybrid+rerank", "hybrid", True),
)
KS = (1, 3, 5, 10)
CUTOFF = 10


def _zip_directory(root: Path) -> io.BytesIO:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                archive.write(path, f"{root.name}/{path.relative_to(root).as_posix()}")
    buffer.seek(0)
    return buffer


def _label_matches(label: dict, entry: IndexEntry, text: str) -> bool:
    if entry.path != label["path"]:
        return False
    symbol = label.get("symbol")
    if symbol:
        entry_symbol = entry.symbol or ""
        # The chunk is the symbol, one of its members, or the class containing it.
        if not (
            entry_symbol == symbol
            or entry_symbol.startswith(symbol + ".")
            or (entry_symbol and symbol.startswith(entry_symbol + "."))
        ):
            return False
    contains = label.get("contains")
    return not contains or contains in text


def _migrate(database_url: str) -> None:
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    get_settings.cache_clear()
    try:
        config = Config(str(BACKEND_DIR / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        get_settings.cache_clear()


def run_benchmark(
    provider: str,
    model: str,
    queries_path: Path = BENCHMARK_DIR / "queries.json",
    cache_dir: str | None = None,
    workdir: Path | None = None,
) -> dict:
    spec = json.loads(queries_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(workdir or tmp)
        database_url = f"sqlite:///{(root / 'benchmark.db').as_posix()}"
        _migrate(database_url)
        base = Settings()
        settings = base.model_copy(
            update={
                "database_url": database_url,
                "workspace_dir": str(root / "workspaces"),
                "chroma_dir": str(root / "chroma"),
                "embedding_provider": provider,
                "embedding_model": model,
                "embedding_cache_dir": cache_dir or base.embedding_cache_dir,
            }
        )
        engine = create_db_engine(database_url)
        session = sessionmaker(bind=engine, expire_on_commit=False)()
        try:
            return _run(session, settings, spec, queries_path.parent)
        finally:
            session.close()
            engine.dispose()


def _run(session, settings: Settings, spec: dict, base_dir: Path) -> dict:
    projects: dict[str, str] = {}
    repo_stats: dict[str, dict] = {}
    for repo, rel in spec["repositories"].items():
        project = import_archive(
            session, settings, _zip_directory(base_dir / rel), name=repo, source_type="zip"
        )
        run_analysis(session, settings, project)
        started = time.perf_counter()
        state = index_project(session, settings, project.id)
        if state.status != "ready":
            raise RuntimeError(f"Indexing {repo} failed: {state.error}")
        projects[repo] = project.id
        repo_stats[repo] = {
            "files": project.file_count,
            "chunks": state.chunk_count,
            "embedded": state.embedded_count,
            "index_seconds": round(time.perf_counter() - started, 3),
        }
    state = session.get(SearchIndex, next(iter(projects.values())))

    # Resolve every label to the manifest entries that satisfy it; a label that matches
    # nothing is a broken benchmark, not a retrieval failure.
    resolved: dict[str, dict[int, set[int]]] = {}
    for query in spec["queries"]:
        pid = projects[query["repo"]]
        entries = session.scalars(select(IndexEntry).where(IndexEntry.project_id == pid)).all()
        texts: dict[int, str] = {}
        for entry in entries:
            lines = split_lines(read_project_file(settings, pid, entry.path))
            texts[entry.id] = "".join(lines[entry.start_line - 1 : entry.end_line])
        satisfied: dict[int, set[int]] = defaultdict(set)
        for index, label in enumerate(query["relevant"]):
            matched = [e.id for e in entries if _label_matches(label, e, texts[e.id])]
            if not matched:
                raise ValueError(f"Label {label} of query {query['id']} matches no chunk.")
            for entry_id in matched:
                satisfied[entry_id].add(index)
        resolved[query["id"]] = satisfied

    configs: dict[str, dict] = {}
    per_query: dict[str, dict] = {q["id"]: {} for q in spec["queries"]}
    for name, mode, rerank in CONFIGS:
        rankings: list[tuple[Ranking, int]] = []
        by_category: dict[str, list[tuple[Ranking, int]]] = defaultdict(list)
        by_repo: dict[str, list[tuple[Ranking, int]]] = defaultdict(list)
        latencies, fallbacks = [], 0
        stages: dict[str, list[float]] = defaultdict(list)
        for query in spec["queries"]:
            pid = projects[query["repo"]]
            started = time.perf_counter()
            outcome = search(
                session, settings, pid, query["query"], mode=mode, limit=CUTOFF, rerank=rerank
            )
            latencies.append((time.perf_counter() - started) * 1000)
            for stage, ms in outcome.timings_ms.items():
                stages[stage].append(ms)
            fallbacks += outcome.mode_used != mode
            satisfied = resolved[query["id"]]
            ranking = [set(satisfied.get(c.entry.id, set())) for c in outcome.results]
            pair = (ranking, len(query["relevant"]))
            rankings.append(pair)
            by_category[query["category"]].append(pair)
            by_repo[query["repo"]].append(pair)
            per_query[query["id"]][name] = {
                "first_relevant_rank": next((i for i, s in enumerate(ranking, 1) if s), None),
                "top3": [
                    f"{c.entry.path}:{c.entry.start_line}-{c.entry.end_line}"
                    + (f" {c.entry.symbol}" if c.entry.symbol else "")
                    for c in outcome.results[:3]
                ],
            }
        latencies.sort()
        configs[name] = {
            "mode": mode,
            "rerank": rerank,
            "metrics": summarize(rankings, KS, CUTOFF),
            "by_category": {
                cat: summarize(pairs, KS, CUTOFF) for cat, pairs in sorted(by_category.items())
            },
            "by_repository": {
                repo: summarize(pairs, KS, CUTOFF) for repo, pairs in sorted(by_repo.items())
            },
            "latency_ms": {
                "mean": round(mean(latencies), 2),
                "p50": round(median(latencies), 2),
                "p95": round(latencies[int(0.95 * (len(latencies) - 1))], 2),
                "stages_mean": {k: round(mean(v), 2) for k, v in stages.items()},
            },
            "fallbacks": fallbacks,
        }

    queries = {q["id"]: q for q in spec["queries"]}
    failures = [
        {
            "id": qid,
            "category": queries[qid]["category"],
            "query": queries[qid]["query"],
            "expected": [
                label["path"] + (f" {label['symbol']}" if label.get("symbol") else "")
                for label in queries[qid]["relevant"]
            ],
            "first_relevant_rank": {name: res["first_relevant_rank"] for name, res in runs.items()},
            "hybrid_rerank_top3": runs["hybrid+rerank"]["top3"],
        }
        for qid, runs in per_query.items()
        if any(r["first_relevant_rank"] is None or r["first_relevant_rank"] > 3
               for r in runs.values())
    ]
    return {
        "evaluated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "benchmark_version": spec.get("version"),
        "query_count": len(spec["queries"]),
        "categories": dict(
            sorted(
                {
                    c: sum(1 for q in spec["queries"] if q["category"] == c)
                    for c in {q["category"] for q in spec["queries"]}
                }.items()
            )
        ),
        "embedding": {
            "provider": state.embedding_provider,
            "model": state.embedding_model,
            "dimension": state.embedding_dimension,
            "input_version": EMBEDDING_INPUT_VERSION,
            "max_input_chars": settings.embedding_max_input_chars,
        },
        "retrieval": {
            "result_limit": CUTOFF,
            "candidate_pool": settings.search_candidate_pool,
            "rrf_k": settings.rrf_k,
            "rrf_weights": {
                "exact": settings.rrf_weight_exact,
                "keyword_identifier_query": settings.rrf_weight_keyword,
                "keyword_natural_query": settings.rrf_weight_keyword_natural,
                "semantic": settings.rrf_weight_semantic,
            },
            "reranker": reranker_module.FeatureReranker.name,
            "reranker_weights": reranker_module.WEIGHTS,
            "chunk_max_lines": settings.chunk_max_lines,
            "chunk_max_chars": settings.chunk_max_chars,
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.processor(),
            "cpu_count": os.cpu_count(),
        },
        "repositories": repo_stats,
        "configs": configs,
        "failures": failures,
        "per_query": per_query,
    }


def to_markdown(report: dict) -> str:
    emb = report["embedding"]
    lines = [
        f"# Retrieval benchmark — {report['evaluated_at']}",
        "",
        f"- Queries: {report['query_count']} ({', '.join(f'{k}: {v}' for k, v in report['categories'].items())})",  # noqa: E501
        f"- Embedding: `{emb['provider']}` / `{emb['model']}` ({emb['dimension']}d, input {emb['input_version']})",  # noqa: E501
        f"- Retrieval: RRF k={report['retrieval']['rrf_k']}, weights {report['retrieval']['rrf_weights']}, "  # noqa: E501
        f"pool {report['retrieval']['candidate_pool']}, reranker {report['retrieval']['reranker']}",
        f"- Environment: Python {report['environment']['python']}, {report['environment']['platform']}, "  # noqa: E501
        f"{report['environment']['cpu_count']} logical CPUs",
        "- Repositories: " + ", ".join(
            f"{name} ({s['files']} files, {s['chunks']} chunks, indexed in {s['index_seconds']} s)"
            for name, s in report["repositories"].items()
        ),
        "",
        "| Configuration | Recall@1 | Recall@3 | Recall@5 | Recall@10 | MRR | Precision@3 | Precision@5 | Hit@5 | Mean latency (ms) | p95 (ms) |",  # noqa: E501
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, cfg in report["configs"].items():
        m = cfg["metrics"]
        lines.append(
            f"| {name} | {m['recall@1']:.3f} | {m['recall@3']:.3f} | {m['recall@5']:.3f} | "
            f"{m['recall@10']:.3f} | {m['mrr']:.3f} | {m['precision@3']:.3f} | "
            f"{m['precision@5']:.3f} | {m['hit@5']:.3f} | {cfg['latency_ms']['mean']:.1f} | "
            f"{cfg['latency_ms']['p95']:.1f} |"
        )
    lines += ["", "## Recall@5 / MRR by repository", ""]
    names = list(report["configs"])
    lines.append("| Repository | " + " | ".join(names) + " |")
    lines.append("| --- |" + " --- |" * len(names))
    for repo in report["repositories"]:
        cells = []
        for name in names:
            m = report["configs"][name]["by_repository"][repo]
            cells.append(f"{m['recall@5']:.2f} / {m['mrr']:.2f}")
        lines.append(f"| {repo} | " + " | ".join(cells) + " |")
    lines += ["", "## Recall@5 / MRR by category", ""]
    names = list(report["configs"])
    lines.append("| Category | " + " | ".join(names) + " |")
    lines.append("| --- |" + " --- |" * len(names))
    for category in report["categories"]:
        cells = []
        for name in names:
            m = report["configs"][name]["by_category"][category]
            cells.append(f"{m['recall@5']:.2f} / {m['mrr']:.2f}")
        lines.append(f"| {category} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## Queries missing a relevant result in the top 3 of any configuration",
        "",
        "First relevant rank per configuration (— = not in the top 10).",
        "",
    ]
    for failure in report["failures"]:
        ranks = ", ".join(
            f"{k}: {v if v is not None else '—'}" for k, v in failure["first_relevant_rank"].items()
        )
        lines.append(f"- **{failure['id']}** ({failure['category']}) “{failure['query']}”")
        lines.append(f"  - expected: {'; '.join(failure['expected'])}")
        lines.append(f"  - ranks: {ranks}")
        lines.append(f"  - hybrid+rerank top 3: {'; '.join(failure['hybrid_rerank_top3'])}")
    return "\n".join(lines) + "\n"


def main() -> None:
    base = Settings()
    parser = argparse.ArgumentParser(description="Run the CodeAtlas retrieval benchmark.")
    parser.add_argument("--provider", default=base.embedding_provider)
    parser.add_argument("--model", default=base.embedding_model)
    parser.add_argument("--output-dir", type=Path, default=BENCHMARK_DIR / "results")
    parser.add_argument("--no-write", action="store_true", help="Print the summary only.")
    args = parser.parse_args()

    report = run_benchmark(args.provider, args.model)
    markdown = to_markdown(report)
    print(markdown)
    if not args.no_write:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^A-Za-z0-9]+", "-", f"{args.provider}-{args.model}").strip("-").lower()
        stem = f"{report['evaluated_at'][:10]}_{slug}"
        (args.output_dir / f"{stem}.json").write_text(json.dumps(report, indent=2) + "\n")
        (args.output_dir / f"{stem}.md").write_text(markdown, encoding="utf-8")
        print(f"Wrote {args.output_dir / stem}.json and .md")


if __name__ == "__main__":
    main()
