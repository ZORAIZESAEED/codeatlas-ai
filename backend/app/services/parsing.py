"""Structural parsing of source files into symbols.

Parsers only build syntax trees; imported code is never executed or imported. Each parser
is registered per language name (see ``languages.py``). Languages without a parser, and
files a parser cannot handle, fall back to line-aware chunking with no symbols.
"""
import ast
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache

import tree_sitter_javascript
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

FALLBACK_PARSER = "line-fallback"
MAX_TEXT = 300


@dataclass
class Symbol:
    name: str
    qualified_name: str
    kind: str  # class | function | method | interface | type | enum | namespace
    start_line: int  # 1-based, inclusive (includes decorators / export keyword)
    end_line: int
    parent: str | None = None
    signature: str | None = None
    docstring: str | None = None


@dataclass
class ParseResult:
    parser: str
    symbols: list[Symbol] = field(default_factory=list)
    error: str | None = None


def _first_line(text: str | None) -> str | None:
    if not text:
        return None
    for line in text.strip().splitlines():
        if line.strip():
            return line.strip()[:MAX_TEXT]
    return None


# --- Python (standard library ast) ---------------------------------------------------------

def parse_python(source: str) -> list[Symbol]:
    with warnings.catch_warnings():  # e.g. SyntaxWarning for invalid escapes in user code
        warnings.simplefilter("ignore")
        tree = ast.parse(source)  # parses only; nothing is compiled or executed
    lines = source.splitlines()
    symbols: list[Symbol] = []

    def visit(body: list[ast.stmt], parent: str | None, in_class: bool) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                kind = "class"
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "method" if in_class else "function"
            else:
                continue
            qualified = f"{parent}.{node.name}" if parent else node.name
            start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
            while start > 1 and lines[start - 2].lstrip().startswith("#"):
                start -= 1  # include the comment block directly above
            symbols.append(
                Symbol(
                    name=node.name,
                    qualified_name=qualified,
                    kind=kind,
                    start_line=start,
                    end_line=node.end_lineno or node.lineno,
                    parent=parent,
                    signature=_first_line(lines[node.lineno - 1]),
                    docstring=_first_line(ast.get_docstring(node)),
                )
            )
            if kind == "class":
                visit(node.body, qualified, True)

    visit(tree.body, None, False)
    return symbols


# --- JavaScript / TypeScript (Tree-sitter) -------------------------------------------------

@cache
def _language(grammar: str) -> Language:
    if grammar == "javascript":
        return Language(tree_sitter_javascript.language())
    if grammar == "typescript":
        return Language(tree_sitter_typescript.language_typescript())
    return Language(tree_sitter_typescript.language_tsx())


DECLARATION_KINDS = {
    "function_declaration": "function",
    "generator_function_declaration": "function",
    "class_declaration": "class",
    "abstract_class_declaration": "class",
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "enum_declaration": "enum",
    "internal_module": "namespace",
    "module": "namespace",
}
FUNCTION_VALUES = {"arrow_function", "function_expression", "function", "generator_function"}
CLASS_VALUES = {"class"}
METHOD_NODES = {"method_definition", "abstract_method_signature"}


def _text(node: Node | None) -> str:
    return node.text.decode("utf-8", errors="replace") if node is not None and node.text else ""


def _signature(node: Node) -> str | None:
    head = _text(node).split("{", 1)[0]
    return _first_line(head)


def _jsdoc(node: Node) -> str | None:
    prev = node.prev_named_sibling
    if prev is None or prev.type != "comment" or prev.end_point[0] < node.start_point[0] - 1:
        return None
    raw = _text(prev)
    if not raw.startswith("/**"):
        return None
    cleaned = [ln.strip().lstrip("/*").rstrip("*/").strip() for ln in raw.splitlines()]
    return _first_line("\n".join(ln for ln in cleaned if ln and not ln.startswith("@")))


def _leading_comment_row(node: Node) -> int:
    """Start row of the comment block directly above ``node`` (or the node's own row)."""
    row = node.start_point[0]
    prev = node.prev_named_sibling
    while prev is not None and prev.type == "comment" and prev.end_point[0] >= row - 1:
        before = prev.prev_sibling
        if before is not None and before.end_point[0] == prev.start_point[0]:
            break  # trailing comment of the previous statement
        row = prev.start_point[0]
        prev = prev.prev_named_sibling
    return row


def _ts_symbols(root: Node) -> list[Symbol]:
    symbols: list[Symbol] = []

    def add(name: str, kind: str, outer: Node, inner: Node, parent: str | None) -> str:
        qualified = f"{parent}.{name}" if parent else name
        symbols.append(
            Symbol(
                name=name[:255],
                qualified_name=qualified,
                kind=kind,
                start_line=_leading_comment_row(outer) + 1,
                end_line=outer.end_point[0] + 1,
                parent=parent,
                signature=_signature(inner),
                docstring=_jsdoc(outer),
            )
        )
        return qualified

    def visit_class_body(body: Node | None, parent: str) -> None:
        if body is None:
            return
        for member in body.named_children:
            name = _text(member.child_by_field_name("name"))
            if member.type in METHOD_NODES and name:
                add(name, "method", member, member, parent)
            elif member.type in ("public_field_definition", "field_definition") and name:
                value = member.child_by_field_name("value")
                if value is not None and value.type in FUNCTION_VALUES:
                    add(name, "method", member, member, parent)

    def visit_declaration(node: Node, outer: Node, parent: str | None) -> None:
        kind = DECLARATION_KINDS.get(node.type)
        if kind:
            name = _text(node.child_by_field_name("name")) or "default"
            qualified = add(name, kind, outer, node, parent)
            body = node.child_by_field_name("body")
            if kind == "class":
                visit_class_body(body, qualified)
            elif kind == "namespace" and body is not None:
                visit_statements(body.named_children, qualified)
        elif node.type in ("lexical_declaration", "variable_declaration"):
            for declarator in node.named_children:
                if declarator.type != "variable_declarator":
                    continue
                value = declarator.child_by_field_name("value")
                name = _text(declarator.child_by_field_name("name"))
                if value is None or not name:
                    continue
                if value.type in FUNCTION_VALUES:
                    add(name, "function", outer, node, parent)
                elif value.type in CLASS_VALUES:
                    qualified = add(name, "class", outer, node, parent)
                    visit_class_body(value.child_by_field_name("body"), qualified)
        elif node.type in ("function_expression", "arrow_function", "class") and outer is not node:
            # export default function () {} / export default class {}
            kind = "class" if node.type == "class" else "function"
            name = _text(node.child_by_field_name("name")) or "default"
            qualified = add(name, kind, outer, node, parent)
            if kind == "class":
                visit_class_body(node.child_by_field_name("body"), qualified)

    def visit_statements(nodes: list[Node], parent: str | None) -> None:
        for node in nodes:
            if node.type == "export_statement":
                inner = node.child_by_field_name("declaration") or node.child_by_field_name(
                    "value"
                )
                if inner is not None:
                    visit_declaration(inner, node, parent)
            elif node.type == "expression_statement" and node.named_child_count == 1:
                # TypeScript `namespace X {}` parses as an expression statement.
                child = node.named_children[0]
                if child.type == "internal_module":
                    visit_declaration(child, node, parent)
            else:
                visit_declaration(node, node, parent)

    visit_statements(root.named_children, None)
    return symbols


def _tree_sitter_parser(grammar: str) -> Callable[[str], ParseResult]:
    def parse(source: str) -> ParseResult:
        tree = Parser(_language(grammar)).parse(source.encode("utf-8"))
        if tree is None:
            raise ValueError("Tree-sitter returned no tree")
        result = ParseResult(parser=f"tree-sitter-{grammar}", symbols=_ts_symbols(tree.root_node))
        if tree.root_node.has_error:
            result.error = "Syntax errors found; symbols are from a partial parse."
        return result

    return parse


def _python_parser(source: str) -> ParseResult:
    return ParseResult(parser="python-ast", symbols=parse_python(source))


# Language name (from the language registry) -> parser. Add entries here to support more.
PARSERS: dict[str, Callable[[str], ParseResult]] = {
    "Python": _python_parser,
    "JavaScript": _tree_sitter_parser("javascript"),
    "JSX": _tree_sitter_parser("javascript"),
    "TypeScript": _tree_sitter_parser("typescript"),
    "TSX": _tree_sitter_parser("tsx"),
}


def parse_source(source: str, language: str) -> ParseResult:
    """Parse ``source``; never raises. Unsupported or unparsable files use the fallback."""
    parser = PARSERS.get(language)
    if parser is None:
        return ParseResult(parser=FALLBACK_PARSER)
    # A UTF-8 byte order mark is not a line break, so stripping it keeps line numbers intact.
    source = source.removeprefix("\ufeff")
    try:
        return parser(source)
    except SyntaxError as exc:
        line = f" (line {exc.lineno})" if exc.lineno else ""
        return ParseResult(parser=FALLBACK_PARSER, error=f"Syntax error{line}: {exc.msg}"[:500])
    except (ValueError, RecursionError, MemoryError) as exc:
        return ParseResult(parser=FALLBACK_PARSER, error=f"Parse failed: {type(exc).__name__}")
