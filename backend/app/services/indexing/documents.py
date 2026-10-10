"""Prepare stored Milestone 2 chunks for indexing. The stored chunk (line range, hash) is the
source of truth; this module only derives index inputs from it and never alters source.

Bump EMBEDDING_INPUT_VERSION whenever ``embedding_text`` changes: indexes built with another
version are reported stale and fully re-embedded on the next indexing run.
"""
import hashlib
from dataclasses import dataclass

from app.services.retrieval.terms import compound_parts

EMBEDDING_INPUT_VERSION = "v1"


@dataclass(frozen=True)
class ChunkRef:
    path: str
    start_line: int
    end_line: int
    chunk_kind: str
    symbol: str | None
    symbol_kind: str | None
    language: str
    content_hash: str


def _sha(*parts: object) -> str:
    return hashlib.sha256("\x00".join(str(p) for p in parts).encode("utf-8")).hexdigest()


def record_id(project_id: str, ref: ChunkRef) -> str:
    """Deterministic ChromaDB record id: same project + chunk location + content => same id."""
    return _sha(
        project_id, ref.path, ref.start_line, ref.end_line, ref.content_hash, ref.chunk_kind,
        ref.symbol or "", ref.symbol_kind or "", ref.language,
    )[:40]


def embedding_text(ref: ChunkRef, code: str, max_chars: int) -> str:
    """Structural header + code. Line numbers are excluded so a chunk that only moved keeps the
    same embedding input (and its vector is reused)."""
    header = [f"File: {ref.path}", f"Language: {ref.language}"]
    if ref.symbol:
        kind = f" ({ref.symbol_kind})" if ref.symbol_kind else ""
        header.append(f"Symbol: {ref.symbol}{kind}")
        words = compound_parts(ref.symbol)
        if words:
            header.append("Name words: " + " ".join(words))
    text = "\n".join(header) + "\nCode:\n"
    # Models truncate at their token limit (512 for the default); cap the code so the header is
    # always inside the window.
    budget = max(200, max_chars - len(text))
    return text + code[:budget]


def embed_hash(text: str) -> str:
    return _sha(EMBEDDING_INPUT_VERSION, text)


def keyword_fields(ref: ChunkRef, code: str) -> tuple[str, str, str]:
    """(path, symbol, body) columns for the FTS5 index. Identifier sub-words are appended so
    ``access token`` matches ``createAccessToken`` while the whole identifier still matches."""
    path_terms = ref.path + " " + " ".join(compound_parts(ref.path))
    symbol_terms = ""
    if ref.symbol:
        symbol_terms = ref.symbol + " " + " ".join(compound_parts(ref.symbol))
    body = code + "\n" + " ".join(compound_parts(code))
    return path_terms, symbol_terms, body
