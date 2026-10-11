"""Static verification of explicit code-relationship claims in answers.

Answers sometimes state relationships such as "`A` calls `B`", "`A` imports `B`" or
"`A` returns `B`". A citation can be valid (it points at real lines) while the relationship is
invented, so these claims are checked against the project's own source with the parsers that
analysis already uses (Python ``ast``, Tree-sitter for JavaScript/TypeScript) and the symbol
table. Nothing is executed and no model is involved.

Verdicts:
- ``verified``: the parser finds the call / import / return in A's definition.
- ``inferred``: plausible but not certain, e.g. a method call on an object whose type is not
  statically known (dynamic dispatch), or B passed as a value (callback, dependency).
- ``unverified``: cannot be decided statically (A is not a known symbol, the language has no
  parser, or A makes dynamic calls that could reach B).
- ``unsupported``: A was found and fully parsed, and contains no call to / import of / return of
  B and no dynamic call that could reach it.

Answers are annotated, never shortened: unsupported claims are labelled as such, inferred and
unverified ones are qualified, verified ones are left unchanged.
"""
import ast
import re
from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from tree_sitter import Node, Parser

from app.core.config import Settings
from app.db.models import CodeSymbol, ProjectFile
from app.services.ingestion import IngestionError, read_project_file
from app.services.parsing import _language
from app.services.rag.citations import cited_ids

VERIFIED, INFERRED, UNVERIFIED, UNSUPPORTED = "verified", "inferred", "unverified", "unsupported"
_RANK = {VERIFIED: 0, INFERRED: 1, UNVERIFIED: 2, UNSUPPORTED: 3}
MAX_CLAIMS = 20
MAX_CANDIDATES = 5
TS_GRAMMARS = {"JavaScript": "javascript", "JSX": "javascript", "TypeScript": "typescript",
               "TSX": "tsx"}

# A code name: identifier, dotted name (`self.x`, `AuthService.authenticate`) or file path.
_N = r"`?([A-Za-z_][A-Za-z0-9_]*(?:[./][A-Za-z0-9_-]+)*)(?:\(\))?`?"
_KIND = r"(?:(?:function|method|class|module|endpoint|route|handler)\s+)?"
_ADV = r"(?:(?:also|then|directly|first|internally|finally|just|only|simply)\s+)?"
_ART = r"(?:(?:the|a|an)\s+)?(?:result\s+of\s+(?:(?:the|a|an)\s+)?)?"
ACTIVE = re.compile(
    rf"{_N}\s+{_KIND}{_ADV}(calls|invokes|imports|returns|delegates\s+to)\s+{_ART}{_KIND}{_N}",
    re.IGNORECASE,
)
PASSIVE = re.compile(
    rf"{_N}\s+{_KIND}(?:is|are)\s+{_ADV}(called|invoked|imported|returned)\s+by\s+{_ART}{_KIND}{_N}",
    re.IGNORECASE,
)
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")
_VERB = {"calls": "calls", "invokes": "calls", "delegates to": "calls", "imports": "imports",
         "returns": "returns", "called": "calls", "invoked": "calls", "imported": "imports",
         "returned": "returns"}


def _code_like(token: str, raw: str) -> bool:
    """Only claims about code names are checked: backticked, called, dotted, snake_case or
    camelCase tokens. Plain English words ("function", "it") are not code names."""
    return ("`" in raw or "()" in raw or "." in token or "_" in token
            or bool(re.search(r"[a-z][A-Z]|^[A-Z][a-z]+[A-Z]", token)))


@dataclass
class Claim:
    subject: str  # A
    relation: str  # calls | imports | returns
    target: str  # B
    sentence_index: int
    text: str


def extract_claims(text: str) -> tuple[list[tuple[int, int]], list[Claim]]:
    """Sentence spans of ``text`` and the relationship claims found in them."""
    spans, start = [], 0
    for m in SENTENCE_BREAK.finditer(text):
        if m.start() > start:
            spans.append((start, m.start()))
        start = m.end()
    if start < len(text):
        spans.append((start, len(text)))
    claims: list[Claim] = []
    seen: set[tuple[str, str, str]] = set()
    for index, (a, b) in enumerate(spans):
        sentence = text[a:b]
        for pattern, passive in ((ACTIVE, False), (PASSIVE, True)):
            for m in pattern.finditer(sentence):
                first, verb, second = m.group(1), m.group(2), m.group(3)
                raw_first = sentence[m.start(1) - 1:m.end(1) + 3]
                raw_second = sentence[m.start(3) - 1:m.end(3) + 3]
                if not (_code_like(first, raw_first) and _code_like(second, raw_second)):
                    continue
                subject, target = (second, first) if passive else (first, second)
                relation = _VERB[" ".join(verb.lower().split())]
                subject, target = _clean(subject), _clean(target)
                key = (subject, relation, target)
                if subject and target and subject != target and key not in seen:
                    seen.add(key)
                    claims.append(Claim(subject, relation, target, index, m.group(0)))
    return spans, claims[:MAX_CLAIMS]


def _clean(name: str) -> str:
    return re.sub(r"^(?:self|this)\.", "", name.strip("`").removesuffix("()"))


# -- static facts --------------------------------------------------------------------------

@dataclass
class Call:
    name: str  # last component of the callee
    dotted: str | None  # full dotted callee when it is a plain name/attribute chain
    receiver: str | None  # source text of the receiver for attribute calls
    line: int


@dataclass
class Facts:
    calls: list[Call] = field(default_factory=list)
    refs: set[str] = field(default_factory=set)  # names used without being called
    returns_top: set[str] = field(default_factory=set)  # callee/name at the top of a return
    returns_any: set[str] = field(default_factory=set)  # any name inside a return value
    has_return: bool = False
    dynamic: list[str] = field(default_factory=list)  # reasons the call graph is open
    imports: set[str] = field(default_factory=set)  # names imported by the file or body


def _py_dotted(node: ast.AST) -> str | None:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _py_callee(call: ast.Call, params: set[str], facts: Facts) -> Call | None:
    f = call.func
    if isinstance(f, ast.Name):
        if f.id in params:
            facts.dynamic.append(f"calls its parameter `{f.id}`")
        if f.id in ("getattr", "eval", "exec", "__import__"):
            facts.dynamic.append(f"uses `{f.id}`")
        return Call(f.id, f.id, None, call.lineno)
    if isinstance(f, ast.Attribute):
        # `obj.m()` names its method, so it is matched by name (dynamic dispatch is handled
        # as "inferred" when the receiver's type is unknown); it cannot directly reach a
        # differently named function.
        return Call(f.attr, _py_dotted(f), ast.unparse(f.value)[:80], call.lineno)
    facts.dynamic.append("calls a computed expression")
    return None


def _py_imports(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for alias in n.names:
                names.update({alias.asname or alias.name, alias.name, alias.name.split(".")[-1]})
        elif isinstance(n, ast.ImportFrom):
            if n.module:
                names.update({n.module, n.module.split(".")[-1]})
            for alias in n.names:
                names.update({alias.asname or alias.name, alias.name})
    return names


def python_facts(source: str, start: int, end: int) -> Facts | None:
    tree = ast.parse(source)
    node = None
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = min([d.lineno for d in n.decorator_list] + [n.lineno])
            if start in (first, n.lineno) and (n.end_lineno or 0) <= end:
                node = n
                break
    if node is None:
        return None
    facts = Facts(imports=_py_imports(tree))
    params: set[str] = set()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        a = node.args
        params = {p.arg for p in a.posonlyargs + a.args + a.kwonlyargs}
        params |= {p.arg for p in (a.vararg, a.kwarg) if p} - {"self", "cls"}
        params -= {"self", "cls"}
    called: set[int] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            call = _py_callee(n, params, facts)
            if call:
                facts.calls.append(call)
                called.add(id(n.func))
        elif isinstance(n, ast.Return) and n.value is not None:
            facts.has_return = True
            top = n.value.func if isinstance(n.value, ast.Call) else n.value
            if isinstance(top, ast.Name):
                facts.returns_top.add(top.id)
            elif isinstance(top, ast.Attribute):
                facts.returns_top.add(top.attr)
            for sub in ast.walk(n.value):
                if isinstance(sub, ast.Name):
                    facts.returns_any.add(sub.id)
                elif isinstance(sub, ast.Attribute):
                    facts.returns_any.add(sub.attr)
    for n in ast.walk(node):
        if id(n) in called:
            continue
        if isinstance(n, ast.Name):
            facts.refs.add(n.id)
        elif isinstance(n, ast.Attribute):
            facts.refs.add(n.attr)
    return facts


def _ts_text(node: Node | None) -> str:
    return node.text.decode("utf-8", errors="replace") if node is not None and node.text else ""


def _ts_find(root: Node, start: int, end: int) -> Node | None:
    best: Node | None = None
    stack = [root]
    while stack:
        node = stack.pop()
        s, e = node.start_point[0] + 1, node.end_point[0] + 1
        if e < start or s > end:
            continue
        if s == start and e <= end and node.is_named and node.type != "comment":
            if best is None or (node.end_byte - node.start_byte) > (best.end_byte
                                                                      - best.start_byte):
                best = node
        stack.extend(node.children)
    return best


def _ts_imports(root: Node) -> set[str]:
    names: set[str] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "import_statement":
            for sub in _walk(node):
                if sub.type in ("identifier", "property_identifier"):
                    names.add(_ts_text(sub))
                elif sub.type == "string_fragment":
                    module = _ts_text(sub)
                    names.update({module, module.rsplit("/", 1)[-1]})
            continue
        if node.type == "call_expression" and _ts_text(node.child_by_field_name("function")) \
                == "require":
            parent = node.parent
            while parent is not None and parent.type not in ("variable_declarator",
                                                             "lexical_declaration"):
                parent = parent.parent
            if parent is not None and parent.type == "variable_declarator":
                pattern = parent.child_by_field_name("name")
                for sub in _walk(pattern):
                    if sub.type in ("identifier", "shorthand_property_identifier_pattern"):
                        names.add(_ts_text(sub))
        stack.extend(node.children)
    return names


def _walk(node: Node | None):
    if node is None:
        return
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(current.children)


def _ts_callee(fn: Node | None, params: set[str], facts: Facts, line: int) -> Call | None:
    if fn is None:
        return None
    if fn.type == "identifier":
        name = _ts_text(fn)
        if name in params:
            facts.dynamic.append(f"calls its parameter `{name}`")
        return Call(name, name, None, line)
    if fn.type == "member_expression":
        prop = _ts_text(fn.child_by_field_name("property"))
        obj = fn.child_by_field_name("object")
        if prop in ("call", "apply", "bind"):
            facts.dynamic.append(f"uses `.{prop}`")
        dotted = _ts_text(fn) if re.fullmatch(r"[\w.]+", _ts_text(fn)) else None
        return Call(prop, dotted, _ts_text(obj)[:80], line)
    facts.dynamic.append("calls a computed expression")
    return None


def ts_facts(source: str, grammar: str, start: int, end: int) -> Facts | None:
    tree = Parser(_language(grammar)).parse(source.encode("utf-8"))
    node = _ts_find(tree.root_node, start, end)
    if node is None:
        return None
    facts = Facts(imports=_ts_imports(tree.root_node))
    params: set[str] = set()
    for sub in _walk(node):
        if sub.type in ("formal_parameters",):
            for p in _walk(sub):
                if p.type == "identifier":
                    params.add(_ts_text(p))
    callee_ids: set[int] = set()
    for sub in _walk(node):
        line = sub.start_point[0] + 1
        if sub.type == "call_expression":
            fn = sub.child_by_field_name("function")
            call = _ts_callee(fn, params, facts, line)
            if call:
                facts.calls.append(call)
                callee_ids.add(fn.id)
                if fn.type == "member_expression":
                    callee_ids.add(fn.child_by_field_name("property").id)
        elif sub.type == "new_expression":
            ctor = sub.child_by_field_name("constructor")
            if ctor is not None:
                facts.calls.append(Call(_ts_text(ctor).split(".")[-1], _ts_text(ctor), None,
                                        line))
                callee_ids.add(ctor.id)
        elif sub.type == "return_statement":
            value = next((c for c in sub.named_children if c.type != "comment"), None)
            if value is None:
                continue
            facts.has_return = True
            top = value
            while top.type in ("await_expression", "parenthesized_expression") and \
                    top.named_children:
                top = top.named_children[0]
            if top.type == "call_expression":
                top = top.child_by_field_name("function")
            elif top.type == "new_expression":
                top = top.child_by_field_name("constructor")
            if top is not None:
                if top.type == "member_expression":
                    facts.returns_top.add(_ts_text(top.child_by_field_name("property")))
                else:
                    facts.returns_top.add(_ts_text(top).split(".")[-1])
            for leaf in _walk(value):
                if leaf.type in ("identifier", "property_identifier"):
                    facts.returns_any.add(_ts_text(leaf))
    for sub in _walk(node):
        if sub.type in ("identifier", "property_identifier") and sub.id not in callee_ids:
            facts.refs.add(_ts_text(sub))
    return facts


# -- verification ----------------------------------------------------------------------------

@dataclass
class RelationshipCheck:
    subject: str
    relation: str
    target: str
    verdict: str
    reason: str
    location: str | None = None  # path:start-end of the subject that was analysed
    claim: str = ""

    def as_dict(self) -> dict:
        return {"subject": self.subject, "relation": self.relation, "target": self.target,
                "verdict": self.verdict, "reason": self.reason, "location": self.location,
                "claim": self.claim}


class _Sources:
    def __init__(self, session: Session, settings: Settings, project_id: str) -> None:
        self.session, self.settings, self.project_id = session, settings, project_id
        self._text: dict[str, str | None] = {}
        self._lang: dict[str, str | None] = {}

    def file(self, path: str) -> tuple[str | None, str | None]:
        if path not in self._text:
            record = self.session.scalar(select(ProjectFile).where(
                ProjectFile.project_id == self.project_id, ProjectFile.path == path))
            text = None
            if record is not None:
                try:
                    text = read_project_file(self.settings, self.project_id, path)
                except (IngestionError, OSError):
                    text = None
            self._text[path] = text
            self._lang[path] = record.language if record else None
        return self._text[path], self._lang[path]

    def file_imports(self, path: str) -> set[str] | None:
        text, language = self.file(path)
        if text is None:
            return None
        text = text.removeprefix("﻿")
        try:
            if language == "Python":
                return _py_imports(ast.parse(text))
            if language in TS_GRAMMARS:
                tree = Parser(_language(TS_GRAMMARS[language])).parse(text.encode("utf-8"))
                return _ts_imports(tree.root_node)
        except (SyntaxError, ValueError, RecursionError):
            return None
        return None

    def facts(self, path: str, start: int, end: int) -> tuple[Facts | None, str | None]:
        """Facts of the definition at path:start-end, or (None, reason)."""
        text, language = self.file(path)
        if text is None:
            return None, f"`{path}` could not be read"
        text = text.removeprefix("﻿")
        try:
            if language == "Python":
                facts = python_facts(text, start, end)
            elif language in TS_GRAMMARS:
                facts = ts_facts(text, TS_GRAMMARS[language], start, end)
            else:
                return None, f"no parser for {language} files"
        except (SyntaxError, ValueError, RecursionError):
            return None, f"`{path}` could not be parsed"
        if facts is None:
            return None, f"the definition at {path}:{start} was not found by the parser"
        return facts, None


def _symbols(session: Session, project_id: str, name: str) -> list[CodeSymbol]:
    escaped = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return list(session.scalars(
        select(CodeSymbol).where(
            CodeSymbol.project_id == project_id,
            or_(CodeSymbol.name == name, CodeSymbol.qualified_name == name,
                CodeSymbol.qualified_name.like("%." + escaped, escape="\\")),
        ).limit(MAX_CANDIDATES)
    ).all())


def _check_one(facts: Facts, relation: str, target: str, subject: str) -> tuple[str, str]:
    last = target.split(".")[-1]
    owner = target.split(".")[-2] if "." in target else None
    if relation == "calls":
        own_class = subject.rsplit(".", 1)[0].split(".")[-1] if "." in subject else None
        dispatch: Call | None = None
        for call in facts.calls:
            if call.name != last:
                continue
            receiver = call.receiver or ""
            known = re.match(r"([A-Z][A-Za-z0-9_]*)(?:\(|$)", receiver)  # Foo(...).m / Foo.m
            is_self = receiver in ("self", "this", "cls")
            if owner is None:
                # The claim names no class, and the code calls a function/method of exactly
                # this name (e.g. `this.store.save()` for "calls `save`").
                return VERIFIED, (f"`{subject}` calls `{call.dotted or call.name}` "
                                  f"(line {call.line})")
            elif (call.dotted and call.dotted.endswith(target)) or (
                    known and known.group(1) == owner) or (is_self and own_class == owner):
                return VERIFIED, (f"`{subject}` calls `{call.dotted or call.name}` "
                                  f"(line {call.line})")
            elif known or is_self:
                continue  # a method of a different, statically known class
            dispatch = dispatch or call
        if dispatch is not None:
            return INFERRED, (f"`{subject}` calls `.{last}()` on `{dispatch.receiver}`, whose "
                              "type is not statically known (dynamic dispatch)")
        if last in facts.refs:
            return INFERRED, (f"`{subject}` references `{last}` without calling it directly; "
                              "it may be called indirectly (e.g. as a callback or dependency)")
        if facts.dynamic:
            return UNVERIFIED, (f"no direct call to `{last}` in `{subject}`, but it "
                                f"{facts.dynamic[0]}, so an indirect call cannot be ruled out")
        return UNSUPPORTED, f"no call to `{last}` appears in `{subject}`"
    if relation == "imports":
        if last in facts.imports or target in facts.imports:
            return VERIFIED, f"`{target}` is imported in the file that defines `{subject}`"
        return UNSUPPORTED, f"the file that defines `{subject}` does not import `{target}`"
    # returns
    if last in facts.returns_top:
        return VERIFIED, f"`{subject}` returns `{last}`"
    if last in facts.returns_any:
        return INFERRED, f"`{last}` appears inside a return value of `{subject}`"
    if not facts.has_return:
        return UNSUPPORTED, f"`{subject}` has no return statement with a value"
    if facts.dynamic:
        return UNVERIFIED, f"`{subject}` returns a value that could not be resolved statically"
    return UNSUPPORTED, f"no return value of `{subject}` involves `{last}`"


def verify_claim(sources: _Sources, claim: Claim, cited_paths: set[str]) -> RelationshipCheck:
    check = RelationshipCheck(claim.subject, claim.relation, claim.target, UNVERIFIED, "",
                              claim=claim.text)
    candidates = _symbols(sources.session, sources.project_id, claim.subject)
    if claim.relation == "imports" and not candidates:
        imports = sources.file_imports(claim.subject)  # "`app/x.py` imports `y`"
        if imports is not None:
            last = claim.target.split(".")[-1]
            ok = last in imports or claim.target in imports
            check.verdict = VERIFIED if ok else UNSUPPORTED
            check.reason = (f"`{claim.subject}` imports `{claim.target}`" if ok else
                            f"`{claim.subject}` does not import `{claim.target}`")
            check.location = claim.subject
            return check
    if not candidates:
        check.reason = f"`{claim.subject}` is not a symbol of this project, so it was not checked"
        return check
    preferred = [c for c in candidates if c.path in cited_paths] or candidates
    best: tuple[str, str, str] | None = None
    for symbol in preferred:
        facts, reason = sources.facts(symbol.path, symbol.start_line, symbol.end_line)
        location = f"{symbol.path}:{symbol.start_line}-{symbol.end_line}"
        verdict, why = (UNVERIFIED, reason) if facts is None else \
            _check_one(facts, claim.relation, claim.target, symbol.qualified_name)
        if best is None or _RANK[verdict] < _RANK[best[0]]:
            best = (verdict, why, location)
        if verdict == VERIFIED:
            break
    check.verdict, check.reason, check.location = best
    return check


@dataclass
class RelationshipReport:
    text: str
    checks: list[RelationshipCheck] = field(default_factory=list)

    def count(self, verdict: str) -> int:
        return sum(1 for c in self.checks if c.verdict == verdict)

    @property
    def warnings(self) -> list[str]:
        out = []
        for c in self.checks:
            if c.verdict == UNSUPPORTED:
                out.append(f"Unsupported relationship claim: `{c.subject}` {c.relation} "
                           f"`{c.target}` is not shown by the source: {c.reason}"
                           + (f" ({c.location})." if c.location else "."))
        return out


def _annotation(check: RelationshipCheck) -> str:
    where = f" ({check.location})" if check.location else ""
    if check.verdict == UNSUPPORTED:
        return f" (Not supported by the source: {check.reason}{where}.)"
    if check.verdict == INFERRED:
        return f" (Inferred, not statically verified: {check.reason}.)"
    return f" (Not statically verified: {check.reason}.)"


def verify_relationships(
    session: Session, settings: Settings, project_id: str, text: str,
    cited_paths_by_sentence: dict[int, set[str]] | None = None,
) -> RelationshipReport:
    """Check explicit relationship claims in ``text`` and annotate the ones that are not
    verified. Sentences are never removed."""
    spans, claims = extract_claims(text)
    report = RelationshipReport(text=text)
    if not claims:
        return report
    sources = _Sources(session, settings, project_id)
    per_sentence: dict[int, list[RelationshipCheck]] = {}
    all_cited = set().union(*(cited_paths_by_sentence or {}).values()) \
        if cited_paths_by_sentence else set()
    for claim in claims:
        cited = (cited_paths_by_sentence or {}).get(claim.sentence_index) or all_cited
        check = verify_claim(sources, claim, cited)
        report.checks.append(check)
        per_sentence.setdefault(claim.sentence_index, []).append(check)
    # Rewrite from the end so earlier offsets stay valid.
    out = text
    for index in sorted(per_sentence, reverse=True):
        checks = [c for c in per_sentence[index] if c.verdict != VERIFIED]
        if not checks:
            continue
        a, b = spans[index]
        worst = max(checks, key=lambda c: _RANK[c.verdict])
        note = "".join(_annotation(c) for c in checks)
        sentence = out[a:b]
        if worst.verdict == UNSUPPORTED:
            sentence = "**Unsupported claim:** " + sentence
        out = out[:a] + sentence + note + out[b:]
    report.text = out
    return report


def cited_paths_by_sentence(text: str, provided: dict) -> dict[int, set[str]]:
    """Paths of the evidence each sentence cites (sentence indexes as in extract_claims)."""
    spans, _ = extract_claims(text)
    result: dict[int, set[str]] = {}
    for index, (a, b) in enumerate(spans):
        paths = {provided[i].path for i in cited_ids(text[a:b]) if i in provided}
        if paths:
            result[index] = paths
    return result
