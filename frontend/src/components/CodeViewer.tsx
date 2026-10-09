"use client";

import { useMemo } from "react";
import { formatBytes, type FileContent } from "@/lib/api";
import { highlight } from "@/lib/highlight";

export default function CodeViewer({ file, loading }: { file: FileContent; loading: boolean }) {
  const html = useMemo(() => highlight(file.content, file.language), [file]);
  const gutter = useMemo(() => {
    const count = Math.max(1, file.content.split("\n").length - (file.content.endsWith("\n") ? 1 : 0));
    return Array.from({ length: count }, (_, i) => i + 1).join("\n");
  }, [file]);

  return (
    <div className={`code-viewer${loading ? " loading" : ""}`}>
      <div className="code-head">
        <span className="mono code-path" title={file.path}>{file.path}</span>
        <span className="muted small">
          {file.language} · {file.line_count.toLocaleString()} lines · {formatBytes(file.size_bytes)}
        </span>
      </div>
      <div className="code-body">
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
