import pytest

from app.services.evaluation.benchmark import CONFIGS, run_benchmark, to_markdown
from app.services.evaluation.metrics import (
    hit_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    summarize,
)

# Two labels; the result at rank 2 satisfies label 0, rank 4 satisfies labels 0 and 1.
RANKING = [set(), {0}, set(), {0, 1}, set()]


def test_recall_counts_each_label_once():
    assert recall_at_k(RANKING, 2, 1) == 0.0
    assert recall_at_k(RANKING, 2, 2) == 0.5
    assert recall_at_k(RANKING, 2, 4) == 1.0
    assert recall_at_k([], 2, 5) == 0.0
    assert recall_at_k(RANKING, 0, 5) == 0.0


def test_precision_divides_by_k():
    assert precision_at_k(RANKING, 2) == 0.5
    assert precision_at_k(RANKING, 5) == 0.4
    assert precision_at_k([{0}], 5) == 0.2  # missing positions are not relevant


def test_reciprocal_rank_and_hits():
    assert reciprocal_rank(RANKING, 10) == 0.5
    assert reciprocal_rank(RANKING, 1) == 0.0
    assert hit_at_k(RANKING, 1) == 0.0 and hit_at_k(RANKING, 2) == 1.0


def test_summarize_means_over_queries():
    summary = summarize([(RANKING, 2), ([{0}], 1)], (1, 5), 10)
    assert summary["mrr"] == pytest.approx((0.5 + 1.0) / 2)
    assert summary["recall@1"] == pytest.approx(0.5)
    assert summary["recall@5"] == pytest.approx(1.0)
    assert summary["precision@5"] == pytest.approx((0.4 + 0.2) / 2)


def test_benchmark_runs_end_to_end_with_fake_provider(tmp_path):
    report = run_benchmark("fake", "fake-hash-64", cache_dir=str(tmp_path / "models"))
    assert report["query_count"] >= 40
    assert report["embedding"]["model"] == "fake-hash-64"
    assert set(report["configs"]) == {name for name, _, _ in CONFIGS}
    for config in report["configs"].values():
        assert config["fallbacks"] == 0
        assert 0.0 <= config["metrics"]["mrr"] <= 1.0
        assert config["latency_ms"]["mean"] > 0
    # Exact identifiers and file names are found by lexical retrieval alone.
    keyword = report["configs"]["keyword"]["by_category"]["keyword_identifier"]
    assert keyword["recall@1"] == 1.0
    markdown = to_markdown(report)
    assert "| hybrid+rerank |" in markdown and "Recall@5" in markdown
