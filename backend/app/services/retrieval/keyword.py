"""Lexical retrieval: exact identifier/path matching and SQLite FTS5 BM25 search.

Queries are reduced to word tokens before they reach FTS5 and every token is double-quoted, so
user input can never inject FTS5 operators; all values are bound parameters.
"""
import logging

from sqlalchemy import bindparam, column, func, literal_column, or_, select, table
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.models import IndexEntry
from app.services.indexing.manifest import fts_project_token
from app.services.retrieval.models import (
    Hit,
    SearchFilters,
    _like_escape,
    apply_filters,
    entries_query,
)
from app.services.retrieval.terms import (
    WORD,
    identifier_candidates,
    path_matches,
    query_terms,
    symbol_match_strength,
)

logger = logging.getLogger(__name__)

MAX_TERMS = 32
# bm25() column weights: project (filter only), path, symbol, body.
BM25_WEIGHTS = "0.0, 3.0, 4.0, 1.0"
_FTS = table("chunk_fts", column("rowid"))


def exact_search(
    session: Session, project_id: str, query: str, filters: SearchFilters, limit: int
) -> list[Hit]:
    """Chunks whose symbol or path is named exactly in the query.
    Strength: 3 = full qualified symbol, 2 = last symbol segment, 1 = file path
    (case rules in ``terms.symbol_match_strength``)."""
    strength: dict[int, float] = {}
    entries: dict[int, IndexEntry] = {}
    for token in identifier_candidates(query)[:MAX_TERMS]:
        lower = token.lower()
        # SQL narrows case-insensitively; the exact case rules are applied below.
        symbol = func.lower(IndexEntry.symbol)
        conditions = [symbol == lower, symbol.like("%." + _like_escape(lower), escape="\\")]
        if "." in token or "/" in token:
            path = func.lower(IndexEntry.path)
            conditions += [path == lower, path.like("%/" + _like_escape(lower), escape="\\")]
        rows = session.scalars(
            entries_query(project_id, filters).where(or_(*conditions)).limit(limit * 4)
        ).all()
        for entry in rows:
            value = symbol_match_strength(token, entry.symbol) or (
                1.0 if path_matches(token, entry.path) else 0.0
            )
            if value and value > strength.get(entry.id, 0):
                strength[entry.id] = value
                entries[entry.id] = entry
    ordered = sorted(
        strength,
        key=lambda i: (-strength[i], entries[i].path, entries[i].start_line, i),
    )[:limit]
    return [Hit(entry_id=i, rank=n, score=strength[i]) for n, i in enumerate(ordered, start=1)]


def fts_match_expression(project_id: str, query: str) -> str | None:
    terms = query_terms(query) or [w.lower() for w in WORD.findall(query)]
    if not terms:
        return None
    quoted = " OR ".join(f'"{t}"' for t in terms[:MAX_TERMS])
    return f'project : "{fts_project_token(project_id)}" AND {{path symbol body}} : ({quoted})'


def keyword_search(
    session: Session, project_id: str, query: str, filters: SearchFilters, limit: int
) -> list[Hit]:
    expression = fts_match_expression(project_id, query)
    if expression is None:
        return []
    allowed = apply_filters(
        select(IndexEntry.id).where(IndexEntry.project_id == project_id), filters
    )
    score = literal_column(f"bm25(chunk_fts, {BM25_WEIGHTS})")
    statement = (
        select(IndexEntry.id, score.label("score"))
        .select_from(IndexEntry)
        .join(_FTS, _FTS.c.rowid == IndexEntry.id)
        .where(literal_column("chunk_fts").op("MATCH")(bindparam("expression")))
        .where(IndexEntry.id.in_(allowed))
        .order_by(score, IndexEntry.id)
        .limit(limit)
    )
    try:
        rows = session.execute(statement, {"expression": expression}).all()
    except OperationalError:
        # Defensive: the expression is built from quoted word tokens only.
        logger.warning("FTS query failed", exc_info=True)
        return []
    return [Hit(entry_id=r[0], rank=n, score=float(r[1])) for n, r in enumerate(rows, start=1)]
