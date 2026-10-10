"""Identifier-aware term handling shared by the keyword indexer and the query parser."""
import re

# Same token definition as the FTS5 tokenizer: Unicode word characters, "_" included.
WORD = re.compile(r"\w+", re.UNICODE)
_PARTS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")

STOPWORDS = frozenset(
    """a an and are as at be by can code do does find for from function functions how i in is
    it its locate me of on or responsible show that the this to where which who why with
    file files handle handled handles implement implemented implements""".split()
)


def split_identifier(word: str) -> list[str]:
    """``createAccessToken`` / ``create_access_token`` / ``HTTPServer2`` -> lower-case parts.
    Returns ``[]`` for a word that has no sub-parts."""
    parts: list[str] = []
    for piece in word.split("_"):
        parts.extend(p.lower() for p in _PARTS.findall(piece))
    parts = [p for p in parts if p]
    return parts if len(parts) > 1 else []


def compound_parts(text: str, limit: int = 2000) -> list[str]:
    """Distinct sub-words of the compound identifiers in ``text`` (bounded), so that searching
    ``access token`` finds ``createAccessToken``."""
    seen: dict[str, None] = {}
    for word in WORD.findall(text):
        for part in split_identifier(word):
            if part not in seen:
                seen[part] = None
                if len(seen) >= limit:
                    return list(seen)
    return list(seen)


def query_terms(query: str) -> list[str]:
    """Lower-case search terms of a query: whole words (minus stopwords) plus identifier parts.
    Only word characters survive, so terms are always safe to quote in an FTS5 query."""
    terms: dict[str, None] = {}
    for word in WORD.findall(query):
        lower = word.lower()
        if lower not in STOPWORDS:
            terms[lower] = None
        for part in split_identifier(word):
            if part not in STOPWORDS:
                terms[part] = None
    return list(terms)


def is_identifier_like(token: str) -> bool:
    """snake_case, camelCase/PascalCase compounds, dotted names and paths. Plain words such as
    "user" are not: they are ordinary English as often as they are symbol names."""
    return (
        "_" in token or "." in token or "/" in token
        or re.search(r"[a-z][A-Z]|[A-Z][a-z]+[A-Z]", token) is not None
    )


def symbol_match_strength(token: str, symbol: str | None) -> float:
    """3 = the full qualified symbol, 2 = its last segment, 0 = no match. Identifier-like
    tokens match case-insensitively; plain words only with identical case, so "user" in a
    question does not select class ``User`` while "login" still finds ``login()``."""
    if not symbol:
        return 0.0
    if is_identifier_like(token):
        token, symbol = token.lower(), symbol.lower()
    if symbol == token:
        return 3.0
    if symbol.endswith("." + token):
        return 2.0
    return 0.0


def path_matches(token: str, path: str) -> bool:
    """The token names the file: its full path or a trailing path (``core/db.py``)."""
    if "." not in token and "/" not in token:
        return False
    token, path = token.lower(), path.lower()
    return path == token or path.endswith("/" + token)


def identifier_candidates(query: str) -> list[str]:
    """Tokens of the query that may name a symbol or file exactly: identifiers
    (``AuthService``, ``create_access_token``) and paths (``database.py``, ``core/db.py``)."""
    out: dict[str, None] = {}
    for token in re.findall(r"[\w./-]+", query):
        token = token.strip("./-")
        if len(token) < 3:
            continue
        if is_identifier_like(token) or (
            token.lower() not in STOPWORDS and token.isidentifier()
        ):
            out[token] = None
    return list(out)
