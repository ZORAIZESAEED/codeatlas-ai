"""Language detection registry for repository scanning.

To support a new file type, add a ``LanguageSpec`` to ``DEFAULT_LANGUAGES``. Detection is
case-insensitive and checks, in order: exact file names, file-name glob patterns, extensions.
Files that match no spec are skipped during import.
"""
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import PurePosixPath


@dataclass(frozen=True)
class LanguageSpec:
    name: str
    extensions: tuple[str, ...] = ()  # including the dot, e.g. ".py"
    filenames: tuple[str, ...] = ()  # exact names, e.g. "Dockerfile"
    patterns: tuple[str, ...] = ()  # glob on the file name, e.g. "Dockerfile.*"


class LanguageRegistry:
    def __init__(self, specs: list[LanguageSpec] | tuple[LanguageSpec, ...] = ()) -> None:
        self._by_extension: dict[str, str] = {}
        self._by_filename: dict[str, str] = {}
        self._patterns: list[tuple[str, str]] = []
        self._names: list[str] = []
        for spec in specs:
            self.register(spec)

    def register(self, spec: LanguageSpec) -> None:
        if len(spec.name) > 30:  # project_files.language is String(30)
            raise ValueError(f"Language name too long: {spec.name!r}")
        for ext in spec.extensions:
            if not ext.startswith("."):
                raise ValueError(f"Extension must start with '.': {ext!r}")
            self._by_extension[ext.lower()] = spec.name
        for filename in spec.filenames:
            self._by_filename[filename.lower()] = spec.name
        self._patterns.extend((p.lower(), spec.name) for p in spec.patterns)
        if spec.name not in self._names:
            self._names.append(spec.name)

    @property
    def names(self) -> list[str]:
        return list(self._names)

    def detect(self, filename: str) -> str | None:
        name = PurePosixPath(filename).name.lower()
        if name in self._by_filename:
            return self._by_filename[name]
        for pattern, language in self._patterns:
            if fnmatchcase(name, pattern):
                return language
        return self._by_extension.get(PurePosixPath(name).suffix)


DEFAULT_LANGUAGES: tuple[LanguageSpec, ...] = (
    # Web & scripting
    LanguageSpec("Python", (".py", ".pyi")),
    LanguageSpec("JavaScript", (".js", ".mjs", ".cjs")),
    LanguageSpec("TypeScript", (".ts", ".mts", ".cts")),
    LanguageSpec("JSX", (".jsx",)),
    LanguageSpec("TSX", (".tsx",)),
    LanguageSpec("HTML", (".html", ".htm")),
    LanguageSpec("CSS", (".css", ".scss", ".sass", ".less")),
    LanguageSpec("Shell", (".sh", ".bash", ".zsh")),
    LanguageSpec("PowerShell", (".ps1", ".psm1", ".psd1")),
    LanguageSpec("Ruby", (".rb",), ("Gemfile", "Rakefile")),
    LanguageSpec("PHP", (".php",)),
    # Compiled languages
    LanguageSpec("Go", (".go",)),
    LanguageSpec("Rust", (".rs",)),
    LanguageSpec("Java", (".java",)),
    LanguageSpec("Kotlin", (".kt", ".kts")),
    LanguageSpec("C", (".c", ".h")),
    LanguageSpec("C++", (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx")),
    LanguageSpec("C#", (".cs",)),
    LanguageSpec("Swift", (".swift",)),
    # Data & configuration
    LanguageSpec("JSON", (".json",)),
    LanguageSpec("YAML", (".yaml", ".yml")),
    LanguageSpec("TOML", (".toml",)),
    LanguageSpec("XML", (".xml",)),
    LanguageSpec("INI", (".ini", ".cfg", ".conf"), (".editorconfig",)),
    LanguageSpec("SQL", (".sql",)),
    LanguageSpec(
        "Dockerfile",
        (".dockerfile",),
        ("Dockerfile", "Containerfile"),
        ("dockerfile.*", "containerfile.*"),
    ),
    LanguageSpec("Makefile", (".mk",), ("Makefile", "GNUmakefile")),
    LanguageSpec(
        "Ignore List", (), (".gitignore", ".dockerignore", ".npmignore", ".gitattributes")
    ),
    # Documentation
    LanguageSpec("Markdown", (".md", ".markdown")),
    LanguageSpec(
        "Text",
        (".txt", ".rst"),
        ("LICENSE", "LICENCE", "NOTICE", "AUTHORS", "CHANGELOG", "README", "CODEOWNERS"),
    ),
)

DEFAULT_REGISTRY = LanguageRegistry(DEFAULT_LANGUAGES)
