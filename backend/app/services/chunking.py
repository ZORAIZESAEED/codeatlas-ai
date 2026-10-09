"""Split source files into line-aligned chunks.

Chunks never split a line. Parsed files are chunked along symbol boundaries (one chunk per
symbol when it fits, otherwise per child symbol, otherwise by line windows). Code between
symbols becomes "module" chunks. Files without symbols use line-aware windows that prefer
blank-line (and, for Markdown, heading) boundaries.
"""
import hashlib
from dataclasses import dataclass

from app.services.parsing import Symbol


@dataclass
class Chunk:
    start_line: int  # 1-based, inclusive
    end_line: int
    kind: str  # symbol | module | text
    symbol: str | None
    text: str

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def split_lines(text: str) -> list[str]:
    """Split on "\\n" only (keeping it), matching ast/Tree-sitter line numbers and line_count.

    ``str.splitlines`` would also break on form feeds, U+2028, U+0085, etc., which would make
    chunk line ranges drift from symbol line ranges.
    """
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


class _Chunker:
    def __init__(self, text: str, language: str, max_lines: int, max_chars: int) -> None:
        self.lines = split_lines(text)
        self.language = language
        self.max_lines = max(1, max_lines)
        self.max_chars = max(1, max_chars)
        # Pieces shorter than this are merged with the next one when the result still fits.
        self.small_lines = max(1, self.max_lines // 4)
        self.chunks: list[Chunk] = []
        self.headings = self._markdown_headings() if language == "Markdown" else set()

    def _markdown_headings(self) -> set[int]:
        headings, in_fence = set(), False
        for number, line in enumerate(self.lines, start=1):
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
            elif not in_fence and line.startswith("#"):
                headings.add(number)
        return headings

    def _segment(self, start: int, end: int) -> str:
        return "".join(self.lines[start - 1 : end])

    def _fits(self, start: int, end: int) -> bool:
        if end - start + 1 > self.max_lines:
            return False
        return len(self._segment(start, end)) <= self.max_chars

    def _emit(self, start: int, end: int, kind: str, symbol: str | None) -> None:
        while end > start and not self.lines[end - 1].strip():
            end -= 1  # trailing blank lines carry no content
        text = self._segment(start, end)
        if not text.strip():
            return
        if not any(ch.isalnum() for ch in text) and self._extend_previous(start, end):
            return  # e.g. a lone closing brace joins the chunk before it
        self.chunks.append(Chunk(start, end, kind, symbol, text))

    def _extend_previous(self, start: int, end: int) -> bool:
        if not self.chunks:
            return False
        prev = self.chunks[-1]
        between = self._segment(prev.end_line + 1, start - 1)
        if prev.end_line >= start or between.strip() or not self._fits(prev.start_line, end):
            return False
        prev.end_line = end
        prev.text = self._segment(prev.start_line, end)
        return True

    def windows(self, start: int, end: int, kind: str, symbol: str | None) -> None:
        """Line-aware windows over [start, end]; a single over-long line is its own chunk."""
        pieces: list[tuple[int, int]] = []
        line = start
        while line <= end:
            # Skip leading blank lines so chunks start on content.
            while line <= end and not self.lines[line - 1].strip():
                line += 1
            if line > end:
                break
            chunk_start, chars, stop = line, 0, line
            while stop <= end:
                current = self.lines[stop - 1]
                if stop > chunk_start:
                    too_long = stop - chunk_start >= self.max_lines
                    if too_long or chars + len(current) > self.max_chars:
                        break
                    if stop in self.headings:
                        break
                chars += len(current)
                stop += 1
            chunk_end = stop - 1
            if stop <= end and stop not in self.headings:
                # Cut early: prefer the last blank line in the second half of the window.
                midpoint = chunk_start + (chunk_end - chunk_start) // 2
                for candidate in range(chunk_end, midpoint, -1):
                    if not self.lines[candidate - 1].strip():
                        chunk_end = candidate
                        break
            pieces.append((chunk_start, chunk_end))
            line = chunk_end + 1

        merged: list[tuple[int, int]] = []
        for piece_start, piece_end in pieces:
            if merged:
                first, last = merged[-1]
                if last - first + 1 < self.small_lines and self._fits(first, piece_end):
                    merged[-1] = (first, piece_end)
                    continue
            merged.append((piece_start, piece_end))
        for piece_start, piece_end in merged:
            self._emit(piece_start, piece_end, kind, symbol)

    def by_symbols(
        self, start: int, end: int, symbols: list[Symbol], all_symbols: list[Symbol],
        context: str | None,
    ) -> None:
        cursor = start
        gap_kind = "symbol" if context else "module"
        for sym in sorted(symbols, key=lambda s: (s.start_line, -s.end_line)):
            if sym.start_line < cursor or sym.end_line > end:
                continue  # overlapping or out-of-range symbol: already covered
            if sym.start_line > cursor:
                self.windows(cursor, sym.start_line - 1, gap_kind, context)
            self.symbol(sym, all_symbols)
            cursor = sym.end_line + 1
        if cursor <= end:
            self.windows(cursor, end, gap_kind, context)

    def symbol(self, sym: Symbol, all_symbols: list[Symbol]) -> None:
        if self._fits(sym.start_line, sym.end_line):
            self._emit(sym.start_line, sym.end_line, "symbol", sym.qualified_name)
            return
        children = [
            s for s in all_symbols
            if s.parent == sym.qualified_name
            and s.start_line >= sym.start_line and s.end_line <= sym.end_line and s is not sym
        ]
        if children:
            self.by_symbols(sym.start_line, sym.end_line, children, all_symbols, sym.qualified_name)
        else:
            self.windows(sym.start_line, sym.end_line, "symbol", sym.qualified_name)


def chunk_source(
    text: str,
    language: str,
    symbols: list[Symbol],
    max_lines: int,
    max_chars: int,
    structured: bool = False,
) -> list[Chunk]:
    """``structured``: the file was parsed as code, so symbol-free code is a "module" chunk."""
    chunker = _Chunker(text, language, max_lines, max_chars)
    total = len(chunker.lines)
    if total == 0:
        return []
    valid = [s for s in symbols if 1 <= s.start_line <= s.end_line <= total]
    top_level = [s for s in valid if s.parent is None]
    if top_level:
        chunker.by_symbols(1, total, top_level, valid, None)
    else:
        chunker.windows(1, total, "module" if structured else "text", None)
    return chunker.chunks
