"use client";

import { useEffect, useState } from "react";
import { api, formatBytes, formatDate, type Overview, type Project } from "@/lib/api";
import FileExplorer from "./FileExplorer";

type Tab = "overview" | "files";

export default function ProjectWorkspace({
  project,
  onDeleted,
}: {
  project: Project;
  onDeleted: (project: Project) => void;
}) {
  const [tab, setTab] = useState<Tab>("overview");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openPath, setOpenPath] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

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
  }, [project.id]);

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
          <OverviewView overview={overview} onOpenFile={openFile} />
        ) : error ? null : (
          <p className="muted">Loading overview…</p>
        )
      ) : (
        <FileExplorer projectId={project.id} initialPath={openPath} />
      )}
    </section>
  );
}

function OverviewView({
  overview,
  onOpenFile,
}: {
  overview: Overview;
  onOpenFile: (path: string) => void;
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

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="stat">
      <span className="muted small">{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
