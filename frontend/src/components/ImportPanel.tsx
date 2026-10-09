"use client";

import { useRef, useState, type FormEvent } from "react";
import { api, type Project } from "@/lib/api";

const MAX_ZIP_BYTES = 20 * 1024 * 1024;
const GITHUB_URL = /^https:\/\/(www\.)?github\.com\/[A-Za-z0-9-]+\/[A-Za-z0-9._-]+?(\.git)?\/?$/;

export default function ImportPanel({ onImported }: { onImported: (project: Project) => void }) {
  const [url, setUrl] = useState("");
  const [githubBusy, setGithubBusy] = useState(false);
  const [githubError, setGithubError] = useState<string | null>(null);

  const [file, setFile] = useState<File | null>(null);
  const [zipName, setZipName] = useState("");
  const [zipBusy, setZipBusy] = useState(false);
  const [zipError, setZipError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const submitGithub = async (event: FormEvent) => {
    event.preventDefault();
    const trimmed = url.trim();
    if (!GITHUB_URL.test(trimmed)) {
      setGithubError("Enter a URL like https://github.com/owner/repository");
      return;
    }
    setGithubBusy(true);
    setGithubError(null);
    try {
      const project = await api.importGithub(trimmed);
      setUrl("");
      onImported(project);
    } catch (error) {
      setGithubError(error instanceof Error ? error.message : "Import failed.");
    } finally {
      setGithubBusy(false);
    }
  };

  const chooseFile = (candidate: File | undefined) => {
    setZipError(null);
    if (!candidate) return;
    if (!candidate.name.toLowerCase().endsWith(".zip")) {
      setZipError("Please choose a .zip file.");
      return;
    }
    if (candidate.size > MAX_ZIP_BYTES) {
      setZipError("ZIP files must be 20 MB or smaller.");
      return;
    }
    setFile(candidate);
  };

  const submitZip = async (event: FormEvent) => {
    event.preventDefault();
    if (!file) {
      setZipError("Choose a ZIP file first.");
      return;
    }
    setZipBusy(true);
    setZipError(null);
    try {
      const project = await api.importZip(file, zipName.trim() || undefined);
      setFile(null);
      setZipName("");
      if (fileInput.current) fileInput.current.value = "";
      onImported(project);
    } catch (error) {
      setZipError(error instanceof Error ? error.message : "Upload failed.");
    } finally {
      setZipBusy(false);
    }
  };

  return (
    <section>
      <div className="page-head">
        <span className="eyebrow">Repository ingestion</span>
        <h1>Import a repository</h1>
        <p className="muted">
          Source files are stored and scanned as plain text — nothing is executed. Common source,
          configuration and documentation files are indexed (Python, JS/TS, Go, Rust, Java, C/C++,
          Dockerfiles, Makefiles, YAML, Markdown, text and more). Dependencies, build output,
          lockfiles, binaries and credential files are skipped.
        </p>
      </div>

      <div className="import-grid">
        <form className="card" onSubmit={submitGithub}>
          <h2>Public GitHub repository</h2>
          <p className="muted small">Downloads the default branch as an archive.</p>
          <label className="field">
            <span>Repository URL</span>
            <input
              type="url"
              placeholder="https://github.com/owner/repository"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              disabled={githubBusy}
              required
            />
          </label>
          {githubError ? <p className="alert alert-error">{githubError}</p> : null}
          <button className="btn btn-primary" type="submit" disabled={githubBusy}>
            {githubBusy ? <><span className="spinner" /> Importing…</> : "Import from GitHub"}
          </button>
        </form>

        <form className="card" onSubmit={submitZip}>
          <h2>Upload a ZIP</h2>
          <p className="muted small">Up to 20 MB. Encrypted or unsafe archives are rejected.</p>
          <div
            className={`dropzone${dragging ? " dragging" : ""}`}
            onClick={() => fileInput.current?.click()}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              chooseFile(e.dataTransfer.files[0]);
            }}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") fileInput.current?.click();
            }}
          >
            {file ? <strong>{file.name}</strong> : <span>Drop a .zip here or click to browse</span>}
            <input
              ref={fileInput}
              type="file"
              accept=".zip,application/zip"
              hidden
              onChange={(e) => chooseFile(e.target.files?.[0])}
            />
          </div>
          <label className="field">
            <span>Project name (optional)</span>
            <input
              type="text"
              maxLength={255}
              placeholder="Defaults to the file name"
              value={zipName}
              onChange={(e) => setZipName(e.target.value)}
              disabled={zipBusy}
            />
          </label>
          {zipError ? <p className="alert alert-error">{zipError}</p> : null}
          <button className="btn btn-primary" type="submit" disabled={zipBusy || !file}>
            {zipBusy ? <><span className="spinner" /> Uploading…</> : "Upload ZIP"}
          </button>
        </form>
      </div>
    </section>
  );
}
