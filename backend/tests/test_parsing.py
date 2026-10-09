import pytest

from app.services.parsing import FALLBACK_PARSER, PARSERS, parse_source

PYTHON = '''"""Module docstring."""
import os


@decorator
class Service(Base):
    """Handles things.

    More detail."""

    x = 1

    def run(self, a: int) -> int:
        def local():
            pass
        return a

    async def stop(self):
        ...

    class Config:
        def load(self):
            pass


async def main():
    pass
'''

TYPESCRIPT = """import x from "y";
/** Adds numbers. */
export function add(a: number, b: number): number { return a + b; }
export default class Store<T> extends Base {
  private items: T[] = [];
  get(i: number) { return this.items[i]; }
  handler = () => {};
}
export const useThing = async () => {};
interface Props { a: string }
export type Id = string | number;
enum Color { Red, Green }
namespace NS { export function inner() {} }
abstract class A { abstract run(): void; }
"""


def by_name(result):
    return {s.qualified_name: s for s in result.symbols}


def test_python_symbols():
    result = parse_source(PYTHON, "Python")
    assert result.parser == "python-ast" and result.error is None
    symbols = by_name(result)
    assert list(symbols) == [
        "Service", "Service.run", "Service.stop", "Service.Config", "Service.Config.load", "main"
    ]
    service = symbols["Service"]
    assert (service.kind, service.start_line, service.end_line) == ("class", 5, 23)  # decorator
    assert service.docstring == "Handles things."
    assert service.signature == "class Service(Base):"
    assert symbols["Service.run"].kind == "method"
    assert symbols["Service.run"].parent == "Service"
    assert symbols["Service.Config"].kind == "class"
    assert symbols["main"].kind == "function"
    assert "local" not in {s.name for s in result.symbols}  # function-local defs are skipped


@pytest.mark.parametrize("language", ["TypeScript", "TSX"])
def test_typescript_symbols(language):
    result = parse_source(TYPESCRIPT, language)
    assert result.parser.startswith("tree-sitter-") and result.error is None
    symbols = by_name(result)
    assert {name: s.kind for name, s in symbols.items()} == {
        "add": "function",
        "Store": "class",
        "Store.get": "method",
        "Store.handler": "method",
        "useThing": "function",
        "Props": "interface",
        "Id": "type",
        "Color": "enum",
        "NS": "namespace",
        "NS.inner": "function",
        "A": "class",
        "A.run": "method",
    }
    assert symbols["add"].docstring == "Adds numbers."
    assert symbols["add"].signature == "function add(a: number, b: number): number"
    assert (symbols["Store"].start_line, symbols["Store"].end_line) == (4, 8)


def test_jsx_symbols():
    source = "export default function App() {\n  return <div onClick={() => 1} />;\n}\n"
    result = parse_source(source, "JSX")
    assert result.parser == "tree-sitter-javascript"
    assert [(s.kind, s.name, s.start_line, s.end_line) for s in result.symbols] == [
        ("function", "App", 1, 3)
    ]


def test_python_syntax_error_falls_back():
    result = parse_source("def broken(:\n    pass\n", "Python")
    assert result.parser == FALLBACK_PARSER
    assert result.symbols == []
    assert result.error.startswith("Syntax error (line 1)")


def test_python_deep_nesting_falls_back():
    result = parse_source("x = " + "(" * 5000 + ")" * 5000 + "\n", "Python")
    assert result.parser == FALLBACK_PARSER
    assert result.error


def test_tree_sitter_partial_parse_reports_error():
    result = parse_source("function ok() {}\nfunction broken( {\n", "JavaScript")
    assert result.parser == "tree-sitter-javascript"
    assert result.error and "partial" in result.error
    assert "ok" in {s.name for s in result.symbols}


def test_unsupported_language_uses_fallback():
    for language in ("Go", "Markdown", "YAML", "Unknown"):
        result = parse_source("package main\n", language)
        assert (result.parser, result.symbols, result.error) == (FALLBACK_PARSER, [], None)


def test_parser_registry_is_extensible(monkeypatch):
    from app.services.parsing import ParseResult, Symbol

    def fake(source):
        return ParseResult(parser="fake", symbols=[Symbol("f", "f", "function", 1, 1)])

    monkeypatch.setitem(PARSERS, "Go", fake)
    assert parse_source("func f() {}\n", "Go").parser == "fake"


def test_parsing_does_not_execute_code(tmp_path):
    marker = tmp_path / "pwned"
    source = f"open({str(marker)!r}, 'w').write('x')\ndef f():\n    pass\n"
    parse_source(source, "Python")
    js = f"require('fs').writeFileSync({str(marker)!r}, 'x');\nfunction f() {{}}\n"
    parse_source(js, "JavaScript")
    assert not marker.exists()
