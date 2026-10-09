import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import c from "highlight.js/lib/languages/c";
import cpp from "highlight.js/lib/languages/cpp";
import csharp from "highlight.js/lib/languages/csharp";
import css from "highlight.js/lib/languages/css";
import dockerfile from "highlight.js/lib/languages/dockerfile";
import go from "highlight.js/lib/languages/go";
import ini from "highlight.js/lib/languages/ini";
import java from "highlight.js/lib/languages/java";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import kotlin from "highlight.js/lib/languages/kotlin";
import makefile from "highlight.js/lib/languages/makefile";
import markdown from "highlight.js/lib/languages/markdown";
import php from "highlight.js/lib/languages/php";
import powershell from "highlight.js/lib/languages/powershell";
import python from "highlight.js/lib/languages/python";
import ruby from "highlight.js/lib/languages/ruby";
import rust from "highlight.js/lib/languages/rust";
import sql from "highlight.js/lib/languages/sql";
import swift from "highlight.js/lib/languages/swift";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";

// Maps backend language names (backend/app/services/languages.py) to highlight.js grammars.
// Languages without an entry (e.g. "Text", "Ignore List") are shown as plain text.
const GRAMMARS = {
  Python: ["python", python],
  JavaScript: ["javascript", javascript],
  JSX: ["javascript", javascript],
  TypeScript: ["typescript", typescript],
  TSX: ["typescript", typescript],
  HTML: ["xml", xml],
  XML: ["xml", xml],
  CSS: ["css", css],
  JSON: ["json", json],
  YAML: ["yaml", yaml],
  TOML: ["ini", ini],
  INI: ["ini", ini],
  Markdown: ["markdown", markdown],
  SQL: ["sql", sql],
  Shell: ["bash", bash],
  PowerShell: ["powershell", powershell],
  Ruby: ["ruby", ruby],
  PHP: ["php", php],
  Go: ["go", go],
  Rust: ["rust", rust],
  Java: ["java", java],
  Kotlin: ["kotlin", kotlin],
  C: ["c", c],
  "C++": ["cpp", cpp],
  "C#": ["csharp", csharp],
  Swift: ["swift", swift],
  Dockerfile: ["dockerfile", dockerfile],
  Makefile: ["makefile", makefile],
} as const;

for (const [name, grammar] of Object.values(GRAMMARS)) {
  if (!hljs.getLanguage(name)) hljs.registerLanguage(name, grammar);
}

// Highlighting very large files can freeze the tab, so fall back to plain text.
const MAX_HIGHLIGHT_CHARS = 200_000;

/** Returns escaped, highlighted HTML (highlight.js escapes all source text), or null. */
export function highlight(code: string, language: string): string | null {
  const entry = GRAMMARS[language as keyof typeof GRAMMARS];
  if (!entry || code.length > MAX_HIGHLIGHT_CHARS) return null;
  try {
    return hljs.highlight(code, { language: entry[0], ignoreIllegals: true }).value;
  } catch {
    return null;
  }
}
