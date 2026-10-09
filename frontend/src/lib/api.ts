export const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type Project = {
  id: string;
  name: string;
  source_type: "github" | "zip" | string;
  source_url: string | null;
  status: string;
  created_at: string;
  file_count: number;
  total_lines: number;
  total_bytes: number;
  skipped_count: number;
  languages: string[];
};

export type ProjectFile = {
  project_id: string;
  path: string;
  language: string;
  size_bytes: number;
  line_count: number;
  content_hash: string;
};

export type FileContent = {
  project_id: string;
  path: string;
  language: string;
  size_bytes: number;
  line_count: number;
  content: string;
};

export type LanguageStat = { language: string; files: number; lines: number; bytes: number };

export type Overview = {
  project: Project;
  languages: LanguageStat[];
  directory_count: number;
  top_level: { name: string; type: "file" | "directory"; file_count: number }[];
  largest_files: ProjectFile[];
};

export class ApiError extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${baseUrl}${path}`, init);
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new ApiError("Cannot reach the CodeAtlas API. Is the backend running?");
  }
  if (!response.ok) {
    let message = `Request failed (HTTP ${response.status})`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") message = body.detail;
      else if (Array.isArray(body.detail)) message = "The request was invalid.";
    } catch {
      // Non-JSON error body: keep the generic message.
    }
    throw new ApiError(message);
  }
  return (response.status === 204 ? undefined : await response.json()) as T;
}

const projectPath = (id: string) => `/api/projects/${encodeURIComponent(id)}`;

export const api = {
  listProjects: (signal?: AbortSignal) => request<Project[]>("/api/projects", { signal }),
  overview: (id: string, signal?: AbortSignal) =>
    request<Overview>(`${projectPath(id)}/overview`, { signal }),
  files: (id: string, signal?: AbortSignal) =>
    request<{ project_id: string; files: ProjectFile[] }>(`${projectPath(id)}/files`, { signal }),
  fileContent: (id: string, path: string, signal?: AbortSignal) =>
    request<FileContent>(
      `${projectPath(id)}/files/content?path=${encodeURIComponent(path)}`,
      { signal },
    ),
  deleteProject: (id: string) => request<void>(projectPath(id), { method: "DELETE" }),
  importGithub: (url: string) =>
    request<Project>("/api/projects/import/github", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    }),
  importZip: (file: File, name?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (name) form.append("name", name);
    return request<Project>("/api/projects/import/zip", { method: "POST", body: form });
  },
};

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
