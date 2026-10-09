"use client";

import { useCallback, useEffect, useState } from "react";
import { api, baseUrl, formatDate, type Project } from "@/lib/api";
import ImportPanel from "./ImportPanel";
import ProjectWorkspace from "./ProjectWorkspace";

type Flash = { kind: "success" | "error"; text: string } | null;

export default function Dashboard() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [apiStatus, setApiStatus] = useState<"checking" | "up" | "down">("checking");
  const [flash, setFlash] = useState<Flash>(null);

  const loadProjects = useCallback(async (signal?: AbortSignal) => {
    try {
      const data = await api.listProjects(signal);
      setProjects(data);
      setListError(null);
    } catch (error) {
      if (error instanceof Error && error.name === "AbortError") return;
      setListError(error instanceof Error ? error.message : "Failed to load projects.");
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${baseUrl}/health`, { signal: controller.signal })
      .then((r) => setApiStatus(r.ok ? "up" : "down"))
      .catch((e: unknown) => {
        if (!(e instanceof Error && e.name === "AbortError")) setApiStatus("down");
      });
    loadProjects(controller.signal);
    return () => controller.abort();
  }, [loadProjects]);

  const selected = projects?.find((p) => p.id === selectedId) ?? null;

  const handleImported = (project: Project) => {
    setFlash({
      kind: "success",
      text: `Imported ${project.name}: ${project.file_count} files indexed` +
        (project.skipped_count ? `, ${project.skipped_count} skipped` : "") +
        (project.analysis_status === "ready"
          ? `; ${project.symbol_count} symbols, ${project.chunk_count} chunks.`
          : "; code analysis did not complete — you can retry from the overview."),
    });
    setSelectedId(project.id);
    loadProjects();
  };

  const handleDeleted = (project: Project) => {
    setFlash({ kind: "success", text: `Deleted ${project.name}.` });
    setSelectedId(null);
    loadProjects();
  };

  return (
    <div className="app">
      <header className="topbar app-topbar">
        <span className="brand">◈ CodeAtlas <span>AI</span></span>
        <span className={`status status-${apiStatus}`}>
          <span className="dot" />
          {apiStatus === "checking" ? "Checking API…" : apiStatus === "up" ? "API connected" : "API unavailable"}
        </span>
      </header>

      <div className="layout">
        <aside className="sidebar">
          <button
            className={`btn btn-primary btn-block${selectedId === null ? " active" : ""}`}
            onClick={() => setSelectedId(null)}
          >
            + Import repository
          </button>
          <h2 className="sidebar-title">
            Projects {projects ? <span className="muted">({projects.length})</span> : null}
          </h2>
          {listError ? <p className="alert alert-error">{listError}</p> : null}
          {projects === null && !listError ? <p className="muted small">Loading projects…</p> : null}
          {projects?.length === 0 ? (
            <p className="muted small">No repositories imported yet.</p>
          ) : null}
          <ul className="project-list">
            {projects?.map((p) => (
              <li key={p.id}>
                <button
                  className={`project-item${p.id === selectedId ? " active" : ""}`}
                  onClick={() => setSelectedId(p.id)}
                >
                  <span className="project-name">{p.name}</span>
                  <span className="project-meta">
                    <span className="badge">{p.source_type === "github" ? "GitHub" : "ZIP"}</span>
                    {p.file_count} files · {formatDate(p.created_at)}
                  </span>
                  <span className="project-langs">{p.languages.slice(0, 4).join(" · ")}</span>
                </button>
              </li>
            ))}
          </ul>
        </aside>

        <main className="workspace">
          {flash ? (
            <div className={`alert alert-${flash.kind}`} role="status">
              <span>{flash.text}</span>
              <button className="link-btn" onClick={() => setFlash(null)} aria-label="Dismiss">✕</button>
            </div>
          ) : null}
          {selected ? (
            <ProjectWorkspace
              key={selected.id}
              project={selected}
              onDeleted={handleDeleted}
              onChanged={() => loadProjects()}
            />
          ) : (
            <ImportPanel onImported={handleImported} />
          )}
        </main>
      </div>
    </div>
  );
}
