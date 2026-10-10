"""Weighted Reciprocal Rank Fusion (Cormack et al., 2009).

score(d) = sum over retrievers m of weight_m / (k + rank_m(d)). Only ranks are used, so BM25 and
cosine distances never need to be put on a common scale. Ties are broken by best single rank,
then by entry id, so ordering is stable.
"""
from app.services.retrieval.models import Hit


def reciprocal_rank_fusion(
    lists: dict[str, list[Hit]], weights: dict[str, float], k: int
) -> list[tuple[int, float, dict[str, int], dict[str, float]]]:
    """Returns (entry_id, fused score, ranks per method, raw scores per method), best first.
    Each entry is counted once per method (its best rank), which deduplicates results."""
    scores: dict[int, float] = {}
    ranks: dict[int, dict[str, int]] = {}
    raw: dict[int, dict[str, float]] = {}
    for method, hits in lists.items():
        weight = weights.get(method, 1.0)
        for hit in hits:
            entry_ranks = ranks.setdefault(hit.entry_id, {})
            if method in entry_ranks:
                continue
            entry_ranks[method] = hit.rank
            raw.setdefault(hit.entry_id, {})[method] = hit.score
            scores[hit.entry_id] = scores.get(hit.entry_id, 0.0) + weight / (k + hit.rank)
    ordered = sorted(scores, key=lambda e: (-scores[e], min(ranks[e].values()), e))
    return [(e, scores[e], ranks[e], raw[e]) for e in ordered]
