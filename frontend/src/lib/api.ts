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
  analysis_status: "pending" | "ready" | "failed" | string;
  analyzed_at: string | null;
  symbol_count: number;
  chunk_count: number;
};

export type ProjectFile = {
  project_id: string;
  path: string;
  language: string;
  size_bytes: number;
  line_count: number;
  content_hash: string;
  parser: string | null;
  parse_error: string | null;
};

export type CodeSymbol = {
  path: string;
  name: string;
  qualified_name: string;
  kind: string;
  parent: string | null;
  start_line: number;
  end_line: number;
  signature: string | null;
  docstring: string | null;
};

export type CodeChunk = {
  path: string;
  chunk_index: number;
  start_line: number;
  end_line: number;
  kind: string;
  symbol: string | null;
  language: string;
  char_count: number;
  content_hash: string;
  content: string | null;
};

export type AnalysisOverview = {
  status: string;
  analyzed_at: string | null;
  symbol_count: number;
  chunk_count: number;
  symbols_by_kind: Record<string, number>;
  files_by_parser: Record<string, number>;
  issue_count: number;
  issues: { path: string; parser: string | null; parse_error: string }[];
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
  analysis: AnalysisOverview;
};

export type IndexState = "pending" | "indexing" | "ready" | "stale" | "failed";

export type IndexStatus = {
  project_id: string;
  status: IndexState | string;
  stale_reasons: string[];
  error: string | null;
  embedding_provider: string | null;
  embedding_model: string | null;
  embedding_dimension: number | null;
  input_version: string | null;
  configured_provider: string;
  configured_model: string;
  indexed_chunk_count: number;
  analysis_chunk_count: number;
  total_chunks: number;
  processed_chunks: number;
  embedded_count: number;
  reused_count: number;
  removed_count: number;
  skipped_count: number;
  started_at: string | null;
  last_indexed_at: string | null;
  duration_ms: number | null;
};

export type SearchMode = "hybrid" | "keyword" | "semantic";

export type SearchRequest = {
  query: string;
  mode: SearchMode;
  limit?: number;
  language?: string | null;
  directory?: string | null;
  symbol_type?: string | null;
};

export type SearchResult = {
  chunk_id: string;
  rank: number;
  file_path: string;
  symbol_name: string | null;
  symbol_type: string | null;
  chunk_kind: string;
  language: string;
  start_line: number;
  end_line: number;
  snippet: string;
  snippet_truncated: boolean;
  retrieval_methods: string[];
  score: number;
  fusion_score: number;
  method_ranks: Record<string, number>;
  raw_scores: Record<string, number>;
  rerank_features: Record<string, number>;
};

export type SearchResponse = {
  project_id: string;
  query: string;
  mode_requested: SearchMode;
  mode_used: SearchMode;
  fallback_reason: string | null;
  reranked: boolean;
  fusion_weights: Record<string, number>;
  index_status: IndexStatus;
  results: SearchResult[];
  citations: { rank: number; file_path: string; start_line: number; end_line: number; reference: string }[];
  warnings: string[];
  candidate_counts: Record<string, number>;
  timings_ms: Record<string, number>;
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
  analyze: (id: string) => request<Project>(`${projectPath(id)}/analyze`, { method: "POST" }),
  symbols: (id: string, path: string, signal?: AbortSignal) =>
    request<{ project_id: string; total: number; symbols: CodeSymbol[] }>(
      `${projectPath(id)}/symbols?limit=1000&path=${encodeURIComponent(path)}`,
      { signal },
    ),
  chunks: (id: string, path: string, signal?: AbortSignal) =>
    request<{ project_id: string; total: number; chunks: CodeChunk[] }>(
      `${projectPath(id)}/chunks?limit=1000&path=${encodeURIComponent(path)}`,
      { signal },
    ),
  indexStatus: (id: string, signal?: AbortSignal) =>
    request<IndexStatus>(`${projectPath(id)}/index-status`, { signal }),
  buildIndex: (id: string) => request<IndexStatus>(`${projectPath(id)}/index`, { method: "POST" }),
  search: (id: string, body: SearchRequest, signal?: AbortSignal) =>
    request<SearchResponse>(`${projectPath(id)}/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    }),
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
