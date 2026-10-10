const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:4000/api";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export function getToken(): string | null {
  return localStorage.getItem("taskboard.token");
}

export function logout(): void {
  localStorage.removeItem("taskboard.token");
  window.location.assign("/login");
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
  });
  if (response.status === 401) {
    logout();
    throw new ApiError(401, "Session expired");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, body.error ?? "Request failed");
  }
  return response.status === 204 ? (undefined as T) : response.json();
}
