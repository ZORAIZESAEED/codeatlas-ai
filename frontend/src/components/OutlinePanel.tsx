"use client";

import { useEffect, useState } from "react";
import { api, type CodeChunk, type CodeSymbol, type ProjectFile } from "@/lib/api";
import type { LineRange } from "./CodeViewer";

type Tab = "symbols" | "chunks";

const KIND_LABEL: Record<string, string> = {
  class: "C",
  function: "ƒ",
  method: "m",
  interface: "I",
  type: "T",
  enum: "E",
  namespace: "N",
};

export default function OutlinePanel({
  projectId,
  file,
  range,
  onSelectRange,
}: {
  projectId: string;
  file: ProjectFile;
  range: LineRange | null;
  onSelectRange: (range: LineRange | null) => void;
}) {
  const [tab, setTab] = useState<Tab>("symbols");
  const [data, setData] = useState<{ path: string; symbols: CodeSymbol[]; chunks: CodeChunk[] } | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    Promise.all([
      api.symbols(projectId, file.path, controller.signal),
      api.chunks(projectId, file.path, controller.signal),
    ])
      .then(([s, c]) => setData({ path: file.path, symbols: s.symbols, chunks: c.chunks }))
      .catch((e: unknown) => {
        if (e instanceof Error && e.name === "AbortError") return;
        setError(e instanceof Error ? e.message : "Failed to load outline.");
      });
    return () => controller.abort();
  }, [projectId, file.path]);

  const current = data?.path === file.path ? data : null;
  const isActive = (start: number, end: number) => range?.start === start && range?.end === end;
  const select = (start: number, end: number) =>
    onSelectRange(isActive(start, end) ? null : { start, end });

  return (
    <aside className="outline-panel">
      <div className="outline-tabs" role="tablist">
        {(["symbols", "chunks"] as const).map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            className={`tab${tab === t ? " active" : ""}`}
            onClick={() => setTab(t)}
          >
            {t === "symbols" ? "Symbols" : "Chunks"}
            {current ? <span className="muted"> {t === "symbols" ? current.symbols.length : current.chunks.length}</span> : null}
          </button>
        ))}
      </div>
      <p className="muted small outline-parser">
        Parser: <span className="mono">{file.parser ?? "not analyzed"}</span>
      </p>
      {file.parse_error ? <p className="alert alert-warn">{file.parse_error}</p> : null}
      {error ? <p className="alert alert-error">{error}</p> : null}
      {!current && !error ? <p className="muted small">Loading…</p> : null}

      {current && tab === "symbols" ? (
        current.symbols.length ? (
          <ul className="outline-list">
            {current.symbols.map((s) => (
              <li key={`${s.qualified_name}:${s.start_line}`}>
                <button
                  className={`outline-item${isActive(s.start_line, s.end_line) ? " active" : ""}`}
                  style={{ paddingLeft: `${8 + (s.qualified_name.split(".").length - 1) * 12}px` }}
                  onClick={() => select(s.start_line, s.end_line)}
                  title={s.signature ?? s.qualified_name}
                >
                  <span className={`kind kind-${s.kind}`}>{KIND_LABEL[s.kind] ?? "·"}</span>
                  <span className="outline-name">{s.name}</span>
                  <span className="muted small">{s.start_line}</span>
                </button>
                {s.docstring && isActive(s.start_line, s.end_line) ? (
                  <p className="outline-doc small muted">{s.docstring}</p>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted small">No symbols in this file.</p>
        )
      ) : null}

      {current && tab === "chunks" ? (
        <ul className="outline-list">
          {current.chunks.map((c) => (
            <li key={c.chunk_index}>
              <button
                className={`outline-item${isActive(c.start_line, c.end_line) ? " active" : ""}`}
                onClick={() => select(c.start_line, c.end_line)}
                title={`${c.char_count} chars · ${c.content_hash.slice(0, 12)}`}
              >
                <span className="muted small">#{c.chunk_index}</span>
                <span className="outline-name">{c.symbol ?? c.kind}</span>
                <span className="muted small">
                  {c.start_line}–{c.end_line}
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </aside>
  );
}
