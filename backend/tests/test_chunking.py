from pathlib import Path

import pytest

from app.services.chunking import chunk_source
from app.services.ingestion import count_lines
from app.services.languages import DEFAULT_REGISTRY
from app.services.parsing import FALLBACK_PARSER, parse_source

BACKEND_DIR = Path(__file__).resolve().parents[1]


def newline_lines(text):
    """Independent reference split: only "\n" ends a line (like ast, Tree-sitter, line_count)."""
    lines = [line + "\n" for line in text.split("\n")]
    if text.endswith("\n") or not text:
        lines.pop()
    else:
        lines[-1] = lines[-1][:-1]
    return lines


def covered_lines(chunks):
    lines = []
    for chunk in chunks:
        lines.extend(range(chunk.start_line, chunk.end_line + 1))
    return lines


def assert_well_formed(text, chunks, symbols=()):
    source_lines = newline_lines(text)
    assert len(source_lines) == count_lines(text)
    covered = covered_lines(chunks)
    assert covered == sorted(covered), "chunks must be ordered"
    assert len(covered) == len(set(covered)), "chunks must not overlap"
    content_lines = {i + 1 for i, line in enumerate(source_lines) if line.strip()}
    assert content_lines <= set(covered), "every non-blank line must be in a chunk"
    for chunk in chunks:
        assert 1 <= chunk.start_line <= chunk.end_line <= len(source_lines)
        assert chunk.text == "".join(source_lines[chunk.start_line - 1 : chunk.end_line])
        assert chunk.text.strip()
    # A whole-symbol chunk must start exactly where its symbol starts.
    by_name = {s.qualified_name: s for s in symbols}
    for chunk in chunks:
        sym = by_name.get(chunk.symbol)
        if sym is not None and chunk.start_line == sym.start_line:
            assert chunk.text == "".join(source_lines[sym.start_line - 1 : chunk.end_line])


def analyze(text, language, max_lines=100, max_chars=4000):
    result = parse_source(text, language)
    chunks = chunk_source(
        text, language, result.symbols, max_lines, max_chars,
        structured=result.parser != FALLBACK_PARSER,
    )
    assert_well_formed(text, chunks, result.symbols)
    return result, chunks


def test_fallback_respects_line_limit():
    text = "".join(f"line {i}\n" for i in range(1, 251))
    chunks = chunk_source(text, "Go", [], max_lines=100, max_chars=100_000)
    assert [(c.start_line, c.end_line) for c in chunks] == [(1, 100), (101, 200), (201, 250)]
    assert {c.kind for c in chunks} == {"text"}
    assert_well_formed(text, chunks)


def test_fallback_prefers_blank_lines():
    block = "".join(f"a{i}\n" for i in range(8))
    text = block + "\n" + block + "\n" + block
    chunks = chunk_source(text, "Text", [], max_lines=12, max_chars=100_000)
    # cut at the blank line (9), not mid-block; the blank line itself is trimmed
    assert [(c.start_line, c.end_line) for c in chunks] == [(1, 8), (10, 17), (19, 26)]
    assert_well_formed(text, chunks)


def test_fallback_respects_char_limit_without_splitting_lines():
    text = "short\n" + "x" * 500 + "\nshort again\n"
    chunks = chunk_source(text, "Text", [], max_lines=100, max_chars=100)
    assert [(c.start_line, c.end_line) for c in chunks] == [(1, 1), (2, 2), (3, 3)]
    assert chunks[1].text == "x" * 500 + "\n"  # an over-long line is kept whole
    assert_well_formed(text, chunks)


def test_markdown_splits_on_headings_outside_code_fences():
    text = "# Title\nintro\n## Usage\n```bash\n# not a heading\nrun\n```\n## API\ntext\n"
    chunks = chunk_source(text, "Markdown", [], max_lines=8, max_chars=100_000)
    assert [(c.start_line, c.end_line) for c in chunks] == [(1, 2), (3, 7), (8, 9)]
    assert_well_formed(text, chunks)


def test_small_sections_are_merged():
    text = "# A\nx\n# B\n# C\ny\n" + "".join(f"## S{i}\n" + "line\n" * 30 for i in range(2))
    chunks = chunk_source(text, "Markdown", [], max_lines=40, max_chars=100_000)
    # A, B and C (tiny) merge with the first long section; the second stays separate.
    assert [(c.start_line, c.end_line) for c in chunks] == [(1, 36), (37, 67)]
    assert_well_formed(text, chunks)


def test_lone_closing_brace_joins_previous_chunk():
    text = (
        "class Big {\n"
        + "".join(f"  m{i}() {{\n" + "    x();\n" * 6 + "  }\n" for i in range(3))
        + "}\n"
    )
    result = parse_source(text, "JavaScript")
    chunks = chunk_source(text, "JavaScript", result.symbols, 12, 100_000, structured=True)
    # The class header keeps its own chunk; the final "}" has no content of its own.
    assert [c.symbol for c in chunks] == ["Big", "Big.m0", "Big.m1", "Big.m2"]
    assert chunks[-1].text.endswith("  }\n}\n")
    assert_well_formed(text, chunks)


def test_leading_comments_belong_to_symbol():
    py = "import os\n\n# Helper for things.\n# More.\ndef helper():\n    pass\n"
    symbols = parse_source(py, "Python").symbols
    assert symbols[0].start_line == 3
    js = "foo(); // trailing\nbar();\n/** Docs. */\n// note\nfunction f() {}\n"
    symbols = parse_source(js, "JavaScript").symbols
    assert (symbols[0].name, symbols[0].start_line, symbols[0].docstring) == ("f", 3, None)
    js2 = "foo(); // trailing\nfunction g() {}\n"
    assert parse_source(js2, "JavaScript").symbols[0].start_line == 2


def test_symbol_chunks_with_module_gaps():
    text = (
        "import os\n\nX = 1\n\n\n"
        "def a():\n    return 1\n\n\n"
        "class B:\n    def m(self):\n        pass\n\n"
        "if __name__ == '__main__':\n    a()\n"
    )
    result = parse_source(text, "Python")
    chunks = chunk_source(text, "Python", result.symbols, 100, 100_000, structured=True)
    assert [(c.kind, c.symbol, c.start_line, c.end_line) for c in chunks] == [
        ("module", None, 1, 3),
        ("symbol", "a", 6, 7),
        ("symbol", "B", 10, 12),
        ("module", None, 14, 15),
    ]
    assert_well_formed(text, chunks)


def test_large_class_is_split_by_methods():
    methods = "".join(
        f"    def m{i}(self):\n" + "".join(f"        x = {j}\n" for j in range(8)) + "\n"
        for i in range(6)
    )
    text = "class Big:\n    '''Doc.'''\n\n" + methods
    result = parse_source(text, "Python")
    chunks = chunk_source(text, "Python", result.symbols, 30, 100_000, structured=True)
    assert chunks[0].kind == "symbol" and chunks[0].symbol == "Big"  # class header
    assert [c.symbol for c in chunks[1:]] == [f"Big.m{i}" for i in range(6)]
    assert all(c.end_line - c.start_line + 1 <= 30 for c in chunks)
    assert_well_formed(text, chunks)


def test_oversized_symbol_without_children_uses_windows():
    body = "".join(f"    x{i} = {i}\n" for i in range(50))
    text = "def huge():\n" + body
    result = parse_source(text, "Python")
    chunks = chunk_source(text, "Python", result.symbols, 20, 100_000, structured=True)
    assert len(chunks) == 3
    assert {c.symbol for c in chunks} == {"huge"}
    assert_well_formed(text, chunks)


def test_structured_file_without_symbols_is_module_chunk():
    text = "import os\nprint(os.name)\n"
    chunks = chunk_source(text, "Python", [], 100, 100_000, structured=True)
    assert [(c.kind, c.start_line, c.end_line) for c in chunks] == [("module", 1, 2)]


def test_empty_and_blank_files_produce_no_chunks():
    assert chunk_source("", "Python", [], 100, 1000) == []
    assert chunk_source("\n\n   \n", "Text", [], 100, 1000) == []


def test_invalid_symbol_ranges_are_ignored():
    from app.services.parsing import Symbol

    text = "a\nb\n"
    bogus = [Symbol("x", "x", "function", 5, 9)]
    chunks = chunk_source(text, "Python", bogus, 100, 1000, structured=True)
    assert [(c.start_line, c.end_line) for c in chunks] == [(1, 2)]


# --- Regression tests from the Milestone 2 technical review ----------------------------------

@pytest.mark.parametrize(
    "language,text,expected",
    [
        # Form feeds are common in older Python sources; U+2028/U+0085 appear inside strings.
        (
            "Python",
            "import os\n\x0c\ndef a():\n    return 1\n\x0c\ndef b():\n    return 2\n",
            {"a": "def a():\n    return 1\n", "b": "def b():\n    return 2\n"},
        ),
        (
            "JavaScript",
            'const s = "a b";\nfunction f() {\n  return s;\n}\n',
            {"f": "function f() {\n  return s;\n}\n"},
        ),
        ("Python", 'x = "\x85"\ndef g():\n    pass\n', {"g": "def g():\n    pass\n"}),
        (
            "TypeScript",
            "function a() {\r\n  return 1;\r\n}\r\nclass B {\r\n  m() {}\r\n}\r\n",
            {"a": "function a() {\r\n  return 1;\r\n}\r\n", "B": "class B {\r\n  m() {}\r\n}\r\n"},
        ),
    ],
    ids=["form-feed", "u2028", "nel", "crlf"],
)
def test_non_newline_line_breaks_do_not_shift_line_ranges(language, text, expected):
    result, chunks = analyze(text, language)
    by_symbol = {c.symbol: c.text for c in chunks if c.kind == "symbol"}
    assert by_symbol == expected
    assert max(c.end_line for c in chunks) <= count_lines(text)


def test_bom_does_not_break_python_parsing():
    text = "﻿# comment\ndef h():\n    pass\n"
    result, chunks = analyze(text, "Python")
    assert result.parser == "python-ast" and result.error is None
    assert [(s.name, s.start_line, s.end_line) for s in result.symbols] == [("h", 1, 3)]
    assert chunks[0].text.startswith("﻿")  # the file content itself is not altered


PY_COMPLEX = '''"""Module."""
import functools


def deco(fn):
    return fn


@deco
@functools.lru_cache(maxsize=None)
def cached(x):
    return x


class Outer:
    """Outer doc."""

    attr = 1

    class Middle:
        class Inner:
            @property
            def value(self):
                return 1

            @staticmethod
            def build():
                return Outer.Middle.Inner()

        def middle_method(self):
            return 2

    @classmethod
    def create(cls):
        return cls()


''' + "def large():\n" + "".join(f"    v{i} = {i}\n" for i in range(150)) + "    return v0\n"


@pytest.mark.parametrize("max_lines", [3, 5, 8, 20, 100])
def test_python_decorators_nested_classes_and_large_functions_lose_no_lines(max_lines):
    result, chunks = analyze(PY_COMPLEX, "Python", max_lines=max_lines)
    names = {s.qualified_name for s in result.symbols}
    assert {
        "deco", "cached", "Outer", "Outer.Middle", "Outer.Middle.Inner",
        "Outer.Middle.Inner.value", "Outer.Middle.Inner.build", "Outer.Middle.middle_method",
        "Outer.create", "large",
    } <= names
    cached = next(s for s in result.symbols if s.name == "cached")
    assert cached.start_line == 9  # first decorator line
    assert all(c.end_line - c.start_line + 1 <= max_lines for c in chunks)
    large = [c for c in chunks if c.symbol == "large"]
    assert large[0].text.startswith("def large():") and large[-1].text.endswith("return v0\n")


TS_COMPLEX = """import { Component } from "x";

@Component({ selector: "app" })
export default class Widget<T> extends Base implements IWidget {
  static count = 0;
  static {
    Widget.count = 1;
  }
  #secret = 1;
  @Input() name: string;

  constructor(private readonly service: Service) {
    super();
  }

  get value(): number {
    return this.#secret;
  }

  set value(v: number) {
    this.#secret = v;
  }

  async *items(): AsyncGenerator<T> {
    for (const x of []) {
      yield x;
    }
  }

  handler = (event: Event): void => {
    console.log(event);
  };

  render() {
    return { a: () => 1, b() { return 2; } };
  }
}

export function overloaded(a: string): string;
export function overloaded(a: number): number;
export function overloaded(a: any): any {
  return a;
}

namespace Outer.Inner {
  export class Deep {
    run() {}
  }
}

export const config = {
  build() {
    return 1;
  },
};

if (process.env.X) {
  function conditional() {}
}
"""


@pytest.mark.parametrize("language", ["TypeScript", "TSX"])
@pytest.mark.parametrize("max_lines", [3, 6, 12, 100])
def test_typescript_blocks_lose_no_lines(language, max_lines):
    result, chunks = analyze(TS_COMPLEX, language, max_lines=max_lines)
    names = {s.qualified_name for s in result.symbols}
    assert {"Widget", "Widget.constructor", "Widget.value", "Widget.items", "Widget.handler",
            "Widget.render", "overloaded"} <= names
    widget = next(s for s in result.symbols if s.qualified_name == "Widget")
    assert widget.start_line == 3  # includes the class decorator
    assert all(c.end_line - c.start_line + 1 <= max_lines for c in chunks)


@pytest.mark.parametrize("max_lines", [4, 10, 100])
def test_javascript_blocks_lose_no_lines(max_lines):
    text = (
        "'use strict';\n"
        "/** Docs. */\n"
        "export async function* gen() {\n  yield 1;\n}\n\n"
        "export default class extends Base {\n  #x = 1;\n  static s() {}\n"
        "  get y() { return 1; }\n}\n\n"
        "const obj = {\n  method() {},\n  arrow: () => {},\n};\n\n"
        "module.exports = function legacy() {\n  return 1;\n};\n\n"
        "describe('suite', () => {\n" + "".join(f"  it('t{i}', () => {{}});\n" for i in range(30))
        + "});\n"
    )
    result, chunks = analyze(text, "JavaScript", max_lines=max_lines)
    names = {s.qualified_name for s in result.symbols}
    assert {"gen", "default", "default.s", "default.y"} <= names


def test_deeply_nested_namespaces_fall_back_without_crashing():
    depth = 600
    text = "".join(f"namespace N{i} {{\n" for i in range(depth)) + "}\n" * depth
    result = parse_source(text, "TypeScript")
    assert result.parser in (FALLBACK_PARSER, "tree-sitter-typescript")
    chunks = chunk_source(
        text, "TypeScript", result.symbols, 100, 4000,
        structured=result.parser != FALLBACK_PARSER,
    )
    assert_well_formed(text, chunks, result.symbols)


SOURCE_DIRS = [BACKEND_DIR / "app", BACKEND_DIR / "tests", BACKEND_DIR.parent / "frontend" / "src"]


@pytest.mark.parametrize("max_lines", [5, 25, 100])
def test_repository_sources_lose_no_lines(max_lines):
    """Property check over this repository's own Python/TS/CSS/Markdown sources."""
    checked = 0
    for root in SOURCE_DIRS:
        for path in sorted(root.rglob("*")):
            language = DEFAULT_REGISTRY.detect(path.name) if path.is_file() else None
            if language is None or "__pycache__" in path.parts:
                continue
            analyze(path.read_text(encoding="utf-8"), language, max_lines=max_lines)
            checked += 1
    assert checked > 20


def test_chunking_is_deterministic():
    first = [(c.start_line, c.end_line, c.kind, c.symbol, c.content_hash)
             for c in analyze(TS_COMPLEX, "TypeScript", max_lines=6)[1]]
    second = [(c.start_line, c.end_line, c.kind, c.symbol, c.content_hash)
              for c in analyze(TS_COMPLEX, "TypeScript", max_lines=6)[1]]
    assert first == second
