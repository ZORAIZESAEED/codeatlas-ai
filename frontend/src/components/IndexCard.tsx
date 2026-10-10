"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, formatDate, type IndexStatus } from "@/lib/api";

const STATUS_LABEL: Record<string, string> = {
  pending: "Not indexed",
  indexing: "Indexing",
  ready: "Ready",
  stale: "Stale",
  failed: "Failed",
};

const POLL_MS = 1500;

export default function IndexCard({
  projectId,
  analysisReady,
}: {
  projectId: string;
  analysisReady: boolean;
}) {
  const [status, setStatus] = useState<IndexStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const poll = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = useCallback(
    (signal?: AbortSignal) =>
      api
        .indexStatus(projectId, signal)
        .then(setStatus)
        .catch((e: unknown) => {
          if (e instanceof Error && e.name === "AbortError") return;
          setError(e instanceof Error ? e.message : "Failed to load index status.");
        }),
    [projectId],
  );

  const stopPolling = () => {
    if (poll.current) clearInterval(poll.current);
    poll.current = null;
  };

  useEffect(() => {
    const controller = new AbortController();
    refresh(controller.signal);
    return () => {
      controller.abort();
      stopPolling();
    };
  }, [refresh]);

  // Indexing started elsewhere (another tab): follow it until it finishes.
  useEffect(() => {
    if (status?.status === "indexing" && !running && !poll.current) {
      poll.current = setInterval(() => refresh(), POLL_MS);
    } else if (status?.status !== "indexing" && !running) {
      stopPolling();
    }
  }, [status?.status, running, refresh]);

  const build = async () => {
    setRunning(true);
    setError(null);
    // The index request runs synchronously; real progress comes from the status endpoint.
    stopPolling();
    poll.current = setInterval(() => refresh(), POLL_MS);
    try {
      const result = await api.buildIndex(projectId);
      setStatus(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Indexing failed.");
      refresh();
    } finally {
      stopPolling();
      setRunning(false);
    }
  };

  const state = running ? "indexing" : status?.status ?? "pending";
  const model = status?.embedding_model ?? status?.configured_model;
  const progress =
    state === "indexing" && status && status.total_chunks > 0
      ? { done: status.processed_chunks, total: status.total_chunks }
      : null;
  const buttonLabel =
    state === "indexing" ? "Indexing…" : state === "pending" ? "Build index" : state === "stale" ? "Update index" : "Reindex";

  return (
    <div className="card analysis-card">
      <div className="analysis-head">
        <div>
          <h2>
            Search index <span className={`pill pill-${state}`}>{STATUS_LABEL[state] ?? state}</span>
          </h2>
          <p className="muted small">
            {status?.last_indexed_at
              ? `Last indexed ${formatDate(status.last_indexed_at)} · ${status.indexed_chunk_count.toLocaleString()} of ${status.analysis_chunk_count.toLocaleString()} chunks`
              : analysisReady
                ? "Build the index to enable keyword, semantic and hybrid code search."
                : "Run code analysis before building the search index."}
          </p>
        </div>
        <button className="btn" onClick={build} disabled={running || state === "indexing" || !analysisReady}>
          {state === "indexing" ? <span className="spinner" /> : null}
          {buttonLabel}
        </button>
      </div>

      {error ? <p className="alert alert-error index-alert">{error}</p> : null}
      {state === "failed" && status?.error && !error ? (
        <p className="alert alert-error index-alert">{status.error}</p>
      ) : null}
      {state === "failed" && status?.last_indexed_at ? (
        <p className="muted small index-note">
          Search still uses the previous complete index ({status.indexed_chunk_count.toLocaleString()} chunks,
          built {formatDate(status.last_indexed_at)}).
        </p>
      ) : null}
      {(state === "stale" || state === "failed") && status?.stale_reasons.length ? (
        <p className="alert alert-warn index-alert">
          {state === "failed" ? "The previous index is also stale: " : ""}
          {status.stale_reasons.join(" ")}
        </p>
      ) : null}

      {progress ? (
        <div className="index-progress">
          <div className="bar">
            <span style={{ width: `${(progress.done / progress.total) * 100}%` }} />
          </div>
          <span className="muted small">
            {progress.done.toLocaleString()} / {progress.total.toLocaleString()} chunks processed
          </span>
        </div>
      ) : state === "indexing" ? (
        <p className="muted small index-note">
          Preparing… The first run loads the embedding model (downloaded once, about 70 MB).
        </p>
      ) : null}

      {status ? (
        <ul className="chip-list index-chips">
          <li className="chip mono" title="Embedding model">
            {status.embedding_provider ?? status.configured_provider} · {model}
            {status.embedding_dimension ? ` · ${status.embedding_dimension}d` : ""}
          </li>
          {status.last_indexed_at || status.total_chunks ? (
            <>
              <li className="chip" title="Chunks embedded in the last run">embedded <strong>{status.embedded_count}</strong></li>
              <li className="chip" title="Chunks already indexed and unchanged (not re-embedded)">unchanged <strong>{Math.max(0, status.processed_chunks - status.embedded_count - status.reused_count)}</strong></li>
              {status.reused_count ? <li className="chip" title="New chunk locations whose identical content reused an existing vector">vectors reused <strong>{status.reused_count}</strong></li> : null}
              <li className="chip" title="Index entries removed in the last run">removed <strong>{status.removed_count}</strong></li>
              {status.skipped_count ? <li className="chip" title="Chunks whose source changed since analysis">skipped <strong>{status.skipped_count}</strong></li> : null}
              {status.duration_ms !== null && state !== "indexing" ? (
                <li className="chip" title="Duration of the last run">{(status.duration_ms / 1000).toFixed(1)} s</li>
              ) : null}
            </>
          ) : null}
        </ul>
      ) : null}
    </div>
  );
}
