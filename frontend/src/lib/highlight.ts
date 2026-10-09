import hljs from "highlight.js/lib/core";
import css from "highlight.js/lib/languages/css";
import ini from "highlight.js/lib/languages/ini";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import markdown from "highlight.js/lib/languages/markdown";
import python from "highlight.js/lib/languages/python";
import sql from "highlight.js/lib/languages/sql";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";

hljs.registerLanguage("css", css);
hljs.registerLanguage("ini", ini);
hljs.registerLanguage("javascript", javascript);
hljs.registerLanguage("json", json);
hljs.registerLanguage("markdown", markdown);
hljs.registerLanguage("python", python);
hljs.registerLanguage("sql", sql);
hljs.registerLanguage("typescript", typescript);
hljs.registerLanguage("xml", xml);
hljs.registerLanguage("yaml", yaml);

// Maps backend language names to highlight.js grammars.
const GRAMMARS: Record<string, string> = {
  Python: "python",
  JavaScript: "javascript",
  JSX: "javascript",
  TypeScript: "typescript",
  TSX: "typescript",
  HTML: "xml",
  CSS: "css",
  JSON: "json",
  YAML: "yaml",
  Markdown: "markdown",
  SQL: "sql",
  TOML: "ini",
};

// Highlighting very large files can freeze the tab, so fall back to plain text.
const MAX_HIGHLIGHT_CHARS = 200_000;

/** Returns escaped, highlighted HTML (highlight.js escapes all source text), or null. */
export function highlight(code: string, language: string): string | null {
  const grammar = GRAMMARS[language];
  if (!grammar || code.length > MAX_HIGHLIGHT_CHARS) return null;
  try {
    return hljs.highlight(code, { language: grammar, ignoreIllegals: true }).value;
  } catch {
    return null;
  }
}
