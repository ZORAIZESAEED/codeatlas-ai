"""Optional reranking of fused candidates.

``FeatureReranker`` is a deterministic, model-free reranker combining the normalized fusion
score with lexical features. Its output orders results; it is not a probability or confidence.
A cross-encoder can be added later by implementing ``Reranker``.
"""
from typing import Protocol

from app.services.retrieval.models import Candidate
from app.services.retrieval.terms import (
    WORD,
    compound_parts,
    identifier_candidates,
    path_matches,
    query_terms,
    symbol_match_strength,
)

# Feature weights, applied on top of the fusion score normalized to [0, 1].
WEIGHTS = {
    "exact_symbol": 0.3,
    "exact_path": 0.2,
    "identifier_overlap": 0.2,
    "term_coverage": 0.1,
}


class Reranker(Protocol):
    name: str

    def rerank(self, query: str, candidates: list[Candidate]) -> list[Candidate]: ...


def _terms(text: str) -> set[str]:
    return {w.lower() for w in WORD.findall(text)} | set(compound_parts(text))


class FeatureReranker:
    name = "features-v1"

    def rerank(self, query: str, candidates: list[Candidate]) -> list[Candidate]:
        if not candidates:
            return candidates
        terms = set(query_terms(query))
        idents = identifier_candidates(query)
        top = max(c.fusion_score for c in candidates) or 1.0
        for c in candidates:
            name_terms = _terms(c.entry.symbol or "") | _terms(c.entry.path)
            features = {
                "base": c.fusion_score / top,
                "exact_symbol": float(
                    any(symbol_match_strength(t, c.entry.symbol) for t in idents)
                ),
                "exact_path": float(any(path_matches(t, c.entry.path) for t in idents)),
                "identifier_overlap": len(terms & name_terms) / len(terms) if terms else 0.0,
                "term_coverage": len(terms & _terms(c.text)) / len(terms) if terms else 0.0,
            }
            c.features = {k: round(v, 4) for k, v in features.items()}
            c.score = features["base"] + sum(w * features[k] for k, w in WEIGHTS.items())
        # Stable: ties keep the fusion order (sorted() is stable).
        return sorted(candidates, key=lambda c: -c.score)
