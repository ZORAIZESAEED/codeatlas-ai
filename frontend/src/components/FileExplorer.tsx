"use client";

import { useEffect, useMemo, useState } from "react";
import { api, type FileContent, type ProjectFile } from "@/lib/api";
import CodeViewer from "./CodeViewer";

type TreeNode = { name: string; path: string; children: TreeNode[]; file?: ProjectFile };

function buildTree(files: ProjectFile[]): TreeNode {
  const root: TreeNode = { name: "", path: "", children: [] };
  for (const file of files) {
    const parts = file.path.split("/");
    let node = root;
    parts.forEach((part, i) => {
      const path = parts.slice(0, i + 1).join("/");
      let child = node.children.find((c) => c.name === part && !c.file === (i < parts.length - 1));
      if (!child) {
        child = { name: part, path, children: [], file: i === parts.length - 1 ? file : undefined };
        node.children.push(child);
      }
      node = child;
    });
  }
  const sort = (node: TreeNode) => {
    node.children.sort((a, b) =>
      !a.file === !b.file ? a.name.localeCompare(b.name) : a.file ? 1 : -1,
    );
    node.children.forEach(sort);
  };
  sort(root);
  return root;
}

function ancestors(path: string): string[] {
  const parts = path.split("/");
  return parts.slice(0, -1).map((_, i) => parts.slice(0, i + 1).join("/"));
}

export default function FileExplorer({
  projectId,
  initialPath,
}: {
  projectId: string;
  initialPath: string | null;
}) {
  const [files, setFiles] = useState<ProjectFile[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set(initialPath ? ancestors(initialPath) : []),
  );
  const [selected, setSelected] = useState<string | null>(initialPath);
  const [content, setContent] = useState<FileContent | null>(null);
  const [contentError, setContentError] = useState<string | null>(null);
  const [loadingContent, setLoadingContent] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    api
      .files(projectId, controller.signal)
      .then((data) => setFiles(data.files))
      .catch((e: unknown) => {
        if (e instanceof Error && e.name === "AbortError") return;
        setError(e instanceof Error ? e.message : "Failed to load files.");
      });
    return () => controller.abort();
  }, [projectId]);

  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    setLoadingContent(true);
    setContentError(null);
    api
      .fileContent(projectId, selected, controller.signal)
      .then((data) => {
        setContent(data);
        setLoadingContent(false);
      })
      .catch((e: unknown) => {
        if (e instanceof Error && e.name === "AbortError") return;
        setContent(null);
        setContentError(e instanceof Error ? e.message : "Failed to load file.");
        setLoadingContent(false);
      });
    return () => controller.abort();
  }, [projectId, selected]);

  const visibleFiles = useMemo(() => {
    const query = filter.trim().toLowerCase();
    return query ? (files ?? []).filter((f) => f.path.toLowerCase().includes(query)) : files ?? [];
  }, [files, filter]);
  const tree = useMemo(() => buildTree(visibleFiles), [visibleFiles]);
  const filtering = filter.trim().length > 0;

  const toggle = (path: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });

  const renderNode = (node: TreeNode, depth: number) => {
    const pad = { paddingLeft: `${8 + depth * 14}px` };
    if (node.file) {
      return (
        <li key={node.path}>
          <button
            className={`tree-item${selected === node.path ? " active" : ""}`}
            style={pad}
            onClick={() => setSelected(node.path)}
            title={node.path}
          >
            <span className="tree-icon">·</span>
            {node.name}
          </button>
        </li>
      );
    }
    const open = filtering || expanded.has(node.path);
    return (
      <li key={node.path}>
        <button className="tree-item tree-dir" style={pad} onClick={() => toggle(node.path)}>
          <span className="tree-icon">{open ? "▾" : "▸"}</span>
          {node.name}
        </button>
        {open ? <ul>{node.children.map((c) => renderNode(c, depth + 1))}</ul> : null}
      </li>
    );
  };

  return (
    <div className="explorer">
      <div className="tree-panel">
        <input
          className="tree-filter"
          type="search"
          placeholder="Filter files…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        {error ? <p className="alert alert-error">{error}</p> : null}
        {files === null && !error ? <p className="muted small">Loading files…</p> : null}
        {files && visibleFiles.length === 0 ? <p className="muted small">No matching files.</p> : null}
        <ul className="tree">{tree.children.map((c) => renderNode(c, 0))}</ul>
      </div>
      <div className="viewer-panel">
        {contentError ? <p className="alert alert-error">{contentError}</p> : null}
        {!selected ? (
          <div className="empty">Select a file to view its source.</div>
        ) : content && content.path === selected ? (
          <CodeViewer file={content} loading={loadingContent} />
        ) : loadingContent ? (
          <div className="empty">Loading {selected}…</div>
        ) : null}
      </div>
    </div>
  );
}
