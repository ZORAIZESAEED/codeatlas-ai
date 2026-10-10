"""Ranking metrics over judged results.

A query's ranking is a list with one entry per returned result: the set of relevance labels that
result satisfies (empty = not relevant). A label is one known-relevant item; several chunks can
satisfy the same label, and it is counted once.
"""
from statistics import mean

Ranking = list[set[int]]


def recall_at_k(ranking: Ranking, label_count: int, k: int) -> float:
    """Share of all relevant labels found in the top k."""
    if label_count == 0:
        return 0.0
    found: set[int] = set()
    for labels in ranking[:k]:
        found |= labels
    return len(found) / label_count


def precision_at_k(ranking: Ranking, k: int) -> float:
    """Share of the top k positions holding a relevant result (missing positions count as
    non-relevant). Meaningful when judgments are complete."""
    return sum(1 for labels in ranking[:k] if labels) / k


def hit_at_k(ranking: Ranking, k: int) -> float:
    return float(any(ranking[:k]))


def reciprocal_rank(ranking: Ranking, cutoff: int) -> float:
    for position, labels in enumerate(ranking[:cutoff], start=1):
        if labels:
            return 1.0 / position
    return 0.0


def summarize(rankings: list[tuple[Ranking, int]], ks: tuple[int, ...], cutoff: int) -> dict:
    """Mean metrics over queries given (ranking, label_count) pairs."""
    if not rankings:
        return {}
    out: dict[str, float] = {
        "mrr": mean(reciprocal_rank(r, cutoff) for r, _ in rankings),
    }
    for k in ks:
        out[f"recall@{k}"] = mean(recall_at_k(r, n, k) for r, n in rankings)
        out[f"precision@{k}"] = mean(precision_at_k(r, k) for r, _ in rankings)
        out[f"hit@{k}"] = mean(hit_at_k(r, k) for r, _ in rankings)
    return {key: round(value, 4) for key, value in out.items()}
