"use client";

import { useEffect, useMemo, useRef } from "react";
import { formatBytes, type FileContent } from "@/lib/api";
import { highlight } from "@/lib/highlight";

export type LineRange = { start: number; end: number };

// Must match `.code-body pre` line-height and vertical padding in globals.css.
const LINE_HEIGHT = 20;
const PADDING_TOP = 12;

export default function CodeViewer({
  file,
  loading,
  range,
}: {
  file: FileContent;
  loading: boolean;
  range?: LineRange | null;
}) {
  const body = useRef<HTMLDivElement>(null);
  const html = useMemo(() => highlight(file.content, file.language), [file]);
  const gutter = useMemo(() => {
    const count = Math.max(1, file.content.split("\n").length - (file.content.endsWith("\n") ? 1 : 0));
    return Array.from({ length: count }, (_, i) => i + 1).join("\n");
  }, [file]);

  useEffect(() => {
    if (range && body.current) {
      body.current.scrollTo({ top: Math.max(0, (range.start - 3) * LINE_HEIGHT), behavior: "smooth" });
    }
  }, [range]);

  return (
    <div className={`code-viewer${loading ? " loading" : ""}`}>
      <div className="code-head">
        <span className="mono code-path" title={file.path}>{file.path}</span>
        <span className="muted small">
          {file.language} · {file.line_count.toLocaleString()} lines · {formatBytes(file.size_bytes)}
          {range ? ` · lines ${range.start}–${range.end}` : ""}
        </span>
      </div>
      <div className="code-body" ref={body}>
        {range ? (
          <div
            className="line-highlight"
            aria-hidden="true"
            style={{
              top: PADDING_TOP + (range.start - 1) * LINE_HEIGHT,
              height: (range.end - range.start + 1) * LINE_HEIGHT,
            }}
          />
        ) : null}
        <pre className="gutter" aria-hidden="true">{gutter}</pre>
        {html !== null ? (
          // highlight.js escapes all source text, so this HTML contains only its own spans.
          <pre className="code hljs"><code dangerouslySetInnerHTML={{ __html: html }} /></pre>
        ) : (
          <pre className="code"><code>{file.content}</code></pre>
        )}
      </div>
    </div>
  );
}
