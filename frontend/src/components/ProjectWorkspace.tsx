"use client";

import { useEffect, useState } from "react";
import {
  api,
  formatBytes,
  formatDate,
  type AnalysisOverview,
  type Overview,
  type Project,
} from "@/lib/api";
import FileExplorer from "./FileExplorer";

type Tab = "overview" | "files";

export default function ProjectWorkspace({
  project,
  onDeleted,
  onChanged,
}: {
  project: Project;
  onDeleted: (project: Project) => void;
  onChanged: () => void;
}) {
  const [tab, setTab] = useState<Tab>("overview");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openPath, setOpenPath] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    api
      .overview(project.id, controller.signal)
      .then(setOverview)
      .catch((e: unknown) => {
        if (e instanceof Error && e.name === "AbortError") return;
        setError(e instanceof Error ? e.message : "Failed to load project.");
      });
    return () => controller.abort();
  }, [project.id, reloadKey]);

  const analyze = async () => {
    setAnalyzing(true);
    setError(null);
    try {
      const updated = await api.analyze(project.id);
      if (updated.analysis_status === "failed") {
        setError("Code analysis failed. Previous results (if any) were kept.");
      }
      setReloadKey((k) => k + 1);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Analysis failed.");
    } finally {
      setAnalyzing(false);
    }
  };

  const openFile = (path: string) => {
    setOpenPath(path);
    setTab("files");
  };

  const remove = async () => {
    if (!window.confirm(`Delete "${project.name}" and its imported files? This cannot be undone.`)) {
      return;
    }
    setDeleting(true);
    try {
      await api.deleteProject(project.id);
      onDeleted(project);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed.");
      setDeleting(false);
    }
  };

  return (
    <section>
      <div className="project-head">
        <div>
          <span className="eyebrow">{project.source_type === "github" ? "GitHub import" : "ZIP upload"}</span>
          <h1>{project.name}</h1>
          <p className="muted small">
            Imported {formatDate(project.created_at)}
            {project.source_url ? (
              <>
                {" · "}
                <a href={project.source_url} target="_blank" rel="noopener noreferrer">
                  {project.source_url}
                </a>
              </>
            ) : null}
          </p>
        </div>
        <button className="btn btn-danger" onClick={remove} disabled={deleting}>
          {deleting ? "Deleting…" : "Delete"}
        </button>
      </div>

      <div className="tabs" role="tablist">
        {(["overview", "files"] as const).map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            className={`tab${tab === t ? " active" : ""}`}
            onClick={() => setTab(t)}
          >
            {t === "overview" ? "Overview" : "Files"}
          </button>
        ))}
      </div>

      {error ? <p className="alert alert-error">{error}</p> : null}

      {tab === "overview" ? (
        overview ? (
          <OverviewView
            overview={overview}
            onOpenFile={openFile}
            onAnalyze={analyze}
            analyzing={analyzing}
          />
        ) : error ? null : (
          <p className="muted">Loading overview…</p>
        )
      ) : (
        <FileExplorer
          key={`${project.id}:${overview?.analysis.analyzed_at ?? ""}`}
          projectId={project.id}
          initialPath={openPath}
        />
      )}
    </section>
  );
}

function OverviewView({
  overview,
  onOpenFile,
  onAnalyze,
  analyzing,
}: {
  overview: Overview;
  onOpenFile: (path: string) => void;
  onAnalyze: () => void;
  analyzing: boolean;
}) {
  const { project, languages } = overview;
  const maxLines = Math.max(1, ...languages.map((l) => l.lines));
  return (
    <div className="overview">
      <div className="stats">
        <Stat label="Files" value={project.file_count.toLocaleString()} />
        <Stat label="Lines" value={project.total_lines.toLocaleString()} />
        <Stat label="Size" value={formatBytes(project.total_bytes)} />
        <Stat label="Folders" value={overview.directory_count.toLocaleString()} />
      </div>
      {project.skipped_count ? (
        <p className="muted small">
          {project.skipped_count} files were skipped (unsupported, binary, oversized, excluded
          folders or sensitive files).
        </p>
      ) : null}

      <AnalysisCard
        analysis={overview.analysis}
        onAnalyze={onAnalyze}
        analyzing={analyzing}
        onOpenFile={onOpenFile}
      />

      <div className="overview-grid">
        <div className="card">
          <h2>Languages</h2>
          <ul className="lang-list">
            {languages.map((l) => (
              <li key={l.language}>
                <div className="lang-row">
                  <span>{l.language}</span>
                  <span className="muted small">
                    {l.files} files · {l.lines.toLocaleString()} lines
                  </span>
                </div>
                <div className="bar">
                  <span style={{ width: `${(l.lines / maxLines) * 100}%` }} />
                </div>
              </li>
            ))}
          </ul>
        </div>

        <div className="card">
          <h2>Folder structure</h2>
          <ul className="entry-list">
            {overview.top_level.map((e) => (
              <li key={e.name}>
                {e.type === "directory" ? (
                  <span>📁 {e.name}/</span>
                ) : (
                  <button className="link-btn" onClick={() => onOpenFile(e.name)}>📄 {e.name}</button>
                )}
                {e.type === "directory" ? <span className="muted small">{e.file_count} files</span> : null}
              </li>
            ))}
          </ul>
        </div>

        <div className="card">
          <h2>Largest files</h2>
          <ul className="entry-list">
            {overview.largest_files.map((f) => (
              <li key={f.path}>
                <button className="link-btn mono" onClick={() => onOpenFile(f.path)} title={f.path}>
                  {f.path}
                </button>
                <span className="muted small">{formatBytes(f.size_bytes)}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

const STATUS_LABEL: Record<string, string> = {
  pending: "Not analyzed",
  ready: "Analyzed",
  failed: "Failed",
};

function AnalysisCard({
  analysis,
  onAnalyze,
  analyzing,
  onOpenFile,
}: {
  analysis: AnalysisOverview;
  onAnalyze: () => void;
  analyzing: boolean;
  onOpenFile: (path: string) => void;
}) {
  const kinds = Object.entries(analysis.symbols_by_kind).sort((a, b) => b[1] - a[1]);
  const parsers = Object.entries(analysis.files_by_parser).sort((a, b) => b[1] - a[1]);
  return (
    <div className="card analysis-card">
      <div className="analysis-head">
        <div>
          <h2>
            Code analysis{" "}
            <span className={`pill pill-${analysis.status}`}>
              {STATUS_LABEL[analysis.status] ?? analysis.status}
            </span>
          </h2>
          <p className="muted small">
            {analysis.analyzed_at
              ? `Last run ${formatDate(analysis.analyzed_at)} · ${analysis.symbol_count.toLocaleString()} symbols · ${analysis.chunk_count.toLocaleString()} chunks`
              : "Structural parsing has not run for this project yet."}
          </p>
        </div>
        <button className="btn" onClick={onAnalyze} disabled={analyzing}>
          {analyzing ? <><span className="spinner" /> Analyzing…</> : analysis.analyzed_at ? "Re-analyze" : "Analyze"}
        </button>
      </div>
      {analysis.analyzed_at ? (
        <div className="analysis-grid">
          <div>
            <h3 className="muted small">Symbols by kind</h3>
            {kinds.length ? (
              <ul className="chip-list">
                {kinds.map(([kind, count]) => (
                  <li key={kind} className="chip">{kind} <strong>{count}</strong></li>
                ))}
              </ul>
            ) : (
              <p className="muted small">No symbols found.</p>
            )}
          </div>
          <div>
            <h3 className="muted small">Files by parser</h3>
            <ul className="chip-list">
              {parsers.map(([parser, count]) => (
                <li key={parser} className="chip mono">{parser} <strong>{count}</strong></li>
              ))}
            </ul>
          </div>
          {analysis.issue_count ? (
            <div className="analysis-issues">
              <h3 className="muted small">Parse issues ({analysis.issue_count})</h3>
              <ul className="entry-list">
                {analysis.issues.map((i) => (
                  <li key={i.path}>
                    <button className="link-btn mono" onClick={() => onOpenFile(i.path)} title={i.path}>
                      {i.path}
                    </button>
                    <span className="muted small issue-text" title={i.parse_error}>{i.parse_error}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="stat">
      <span className="muted small">{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
