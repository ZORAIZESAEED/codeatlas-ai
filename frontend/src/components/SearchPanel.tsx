"use client";

import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { api, type SearchMode, type SearchResponse, type SearchResult } from "@/lib/api";
import { highlight } from "@/lib/highlight";
import type { LineRange } from "./CodeViewer";

const MODES: { value: SearchMode; label: string; hint: string }[] = [
  { value: "hybrid", label: "Hybrid", hint: "Exact names + keywords + semantic vectors, fused" },
  { value: "keyword", label: "Keyword", hint: "Exact identifiers/paths and BM25 full-text search" },
  { value: "semantic", label: "Semantic", hint: "Embedding similarity only" },
];

const SYMBOL_TYPES = ["function", "method", "class", "interface", "type", "enum", "module", "text"];

const METHOD_LABEL: Record<string, string> = { exact: "exact", keyword: "keyword", semantic: "semantic" };

export default function SearchPanel({
  projectId,
  languages,
  onOpen,
}: {
  projectId: string;
  languages: string[];
  onOpen: (path: string, range: LineRange) => void;
}) {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<SearchMode>("hybrid");
  const [language, setLanguage] = useState("");
  const [directory, setDirectory] = useState("");
  const [symbolType, setSymbolType] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [response, setResponse] = useState<SearchResponse | null>(null);
  const controller = useRef<AbortController | null>(null);

  useEffect(() => () => controller.current?.abort(), []);

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!query.trim()) return;
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setLoading(true);
    setError(null);
    try {
      const data = await api.search(
        projectId,
        {
          query,
          mode,
          limit: 15,
          language: language || null,
          directory: directory.trim() || null,
          symbol_type: symbolType || null,
        },
        current.signal,
      );
      setResponse(data);
    } catch (e) {
      if (e instanceof Error && e.name === "AbortError") return;
      setResponse(null);
      setError(e instanceof Error ? e.message : "Search failed.");
    } finally {
      if (controller.current === current) setLoading(false);
    }
  };

  return (
    <div className="search">
      <form className="search-form card" onSubmit={submit} role="search">
        <div className="search-row">
          <input
            className="search-input"
            type="search"
            value={query}
            maxLength={500}
            placeholder="Ask where something happens, e.g. “Where is authentication handled?” or an identifier"
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Search query"
          />
          <button className="btn btn-primary" type="submit" disabled={loading || !query.trim()}>
            {loading ? <><span className="spinner" /> Searching…</> : "Search"}
          </button>
        </div>
        <div className="search-options">
          <div className="segmented" role="radiogroup" aria-label="Retrieval mode">
            {MODES.map((m) => (
              <button
                key={m.value}
                type="button"
                role="radio"
                aria-checked={mode === m.value}
                className={mode === m.value ? "active" : ""}
                title={m.hint}
                onClick={() => setMode(m.value)}
              >
                {m.label}
              </button>
            ))}
          </div>
          <select value={language} onChange={(e) => setLanguage(e.target.value)} aria-label="Language filter">
            <option value="">All languages</option>
            {languages.map((l) => (
              <option key={l} value={l}>{l}</option>
            ))}
          </select>
          <select value={symbolType} onChange={(e) => setSymbolType(e.target.value)} aria-label="Symbol type filter">
            <option value="">All symbol types</option>
            {SYMBOL_TYPES.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
          <input
            className="search-dir"
            value={directory}
            maxLength={1024}
            placeholder="Directory, e.g. app/api"
            onChange={(e) => setDirectory(e.target.value)}
            aria-label="Directory filter"
          />
        </div>
        <p className="muted small search-mode-hint">
          Mode: <strong>{MODES.find((m) => m.value === mode)?.label}</strong> —{" "}
          {MODES.find((m) => m.value === mode)?.hint}
        </p>
      </form>

      {error ? <p className="alert alert-error">{error}</p> : null}
      {response ? <ResponseView response={response} onOpen={onOpen} /> : null}
      {!response && !error && !loading ? (
        <div className="empty">
          Results are real source chunks from this project&apos;s search index, with file paths and
          line ranges. Click a result to open it in the code viewer.
        </div>
      ) : null}
    </div>
  );
}

function ResponseView({
  response,
  onOpen,
}: {
  response: SearchResponse;
  onOpen: (path: string, range: LineRange) => void;
}) {
  const fellBack = response.mode_used !== response.mode_requested;
  return (
    <div className="search-results">
      <p className="muted small search-meta">
        {response.results.length} result{response.results.length === 1 ? "" : "s"} · mode used{" "}
        <strong>{response.mode_used}</strong>
        {response.reranked ? " + rerank" : ""} (retrievers:{" "}
        {Object.keys(response.fusion_weights).join(", ") || "none"}) ·{" "}
        {response.timings_ms.total?.toFixed(0) ?? "?"} ms ·
        index {response.index_status.embedding_model ?? "—"}
      </p>
      {fellBack ? (
        <p className="alert alert-warn">
          Requested {response.mode_requested} search, but only {response.mode_used} retrieval ran.{" "}
          {response.fallback_reason}
        </p>
      ) : null}
      {response.warnings
        .filter((w) => !fellBack || !w.startsWith("Semantic search was unavailable"))
        .map((w) => (
          <p key={w} className="alert alert-warn">{w}</p>
        ))}
      {response.results.length === 0 ? (
        <div className="empty">No matching code was found. Try other words or another mode.</div>
      ) : (
        <ol className="result-list">
          {response.results.map((r) => (
            <ResultCard key={r.chunk_id} result={r} onOpen={onOpen} />
          ))}
        </ol>
      )}
    </div>
  );
}

function ResultCard({
  result,
  onOpen,
}: {
  result: SearchResult;
  onOpen: (path: string, range: LineRange) => void;
}) {
  const html = useMemo(() => highlight(result.snippet, result.language), [result]);
  const gutter = useMemo(() => {
    const count = result.snippet.replace(/\n$/, "").split("\n").length;
    return Array.from({ length: count }, (_, i) => result.start_line + i).join("\n");
  }, [result]);
  const open = () => onOpen(result.file_path, { start: result.start_line, end: result.end_line });
  const ranks = Object.entries(result.method_ranks)
    .map(([m, rank]) => `${m} #${rank}`)
    .join(" · ");

  return (
    <li className="result card">
      <div className="result-head">
        <span className="result-rank">{result.rank}</span>
        <div className="result-title">
          <button className="link-btn mono result-path" onClick={open} title={`Open ${result.file_path}`}>
            {result.file_path}:{result.start_line}–{result.end_line}
          </button>
          <span className="result-sub">
            {result.symbol_name ? <span className="mono">{result.symbol_name}</span> : <span className="muted">{result.chunk_kind} chunk</span>}
            {result.symbol_type ? <span className="chip">{result.symbol_type}</span> : null}
            <span className="muted small">{result.language}</span>
          </span>
        </div>
        <div className="result-methods">
          {result.retrieval_methods.map((m) => (
            <span key={m} className={`method method-${m}`}>{METHOD_LABEL[m] ?? m}</span>
          ))}
        </div>
      </div>
      <button className="snippet" onClick={open} title="Open in code viewer">
        <pre className="gutter" aria-hidden="true">{gutter}</pre>
        {html !== null ? (
          // highlight.js escapes all source text, so this HTML contains only its own spans.
          <pre className="code hljs"><code dangerouslySetInnerHTML={{ __html: html }} /></pre>
        ) : (
          <pre className="code"><code>{result.snippet}</code></pre>
        )}
      </button>
      <p className="muted small result-foot" title="Ranking score from Reciprocal Rank Fusion (plus reranking features). It orders results; it is not a confidence or probability.">
        {result.snippet_truncated ? "Snippet truncated · " : ""}
        {ranks} · ranking score {result.score.toFixed(3)}
      </p>
    </li>
  );
}
