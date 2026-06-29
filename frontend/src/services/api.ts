const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";
export const AUTH_TOKEN_STORAGE_KEY = "print_nesting_auth_token";
export const AUTH_FAILURE_EVENT = "print-nesting-auth-failure";

export type AuthFailureDetail = {
  path: string;
  status: number;
};

export class ApiError extends Error {
  status: number;
  path: string;
  detail: unknown;

  constructor(message: string, status: number, path: string, detail: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.path = path;
    this.detail = detail;
  }
}

export function getStoredAuthToken(): string {
  if (typeof window === "undefined") {
    return "";
  }
  return window.localStorage.getItem(AUTH_TOKEN_STORAGE_KEY) || "";
}

export function setStoredAuthToken(token: string): void {
  if (typeof window !== "undefined") {
    window.localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token);
  }
}

export function clearStoredAuthToken(): void {
  if (typeof window !== "undefined") {
    window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
  }
}

function emitAuthFailure(detail: AuthFailureDetail): void {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent<AuthFailureDetail>(AUTH_FAILURE_EVENT, { detail }));
  }
}

export async function apiFetch(path: string, options: RequestInit = {}): Promise<Response> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const token = getStoredAuthToken();
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (token && response.status === 401) {
    clearStoredAuthToken();
    emitAuthFailure({ path, status: response.status });
  }
  return response;
}

export async function apiErrorFromResponse(response: Response, path: string): Promise<ApiError> {
  const raw = await response.text();
  if (!raw) {
    return new ApiError(response.statusText || `HTTP ${response.status}`, response.status, path, null);
  }
  try {
    const payload = JSON.parse(raw) as Record<string, unknown>;
    if ("detail" in payload) {
      return new ApiError(formatApiDetail(payload.detail), response.status, path, payload.detail);
    }
    if (typeof payload.message === "string") {
      return new ApiError(payload.message, response.status, path, payload);
    }
    return new ApiError(JSON.stringify(payload), response.status, path, payload);
  } catch {
    return new ApiError(raw, response.status, path, raw);
  }
}

export async function apiRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await apiFetch(path, options);
  if (!response.ok) {
    throw await apiErrorFromResponse(response, path);
  }
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("image/svg+xml")) {
    return (await response.text()) as T;
  }
  return (await response.json()) as T;
}

function formatApiDetail(detail: unknown): string {
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail)) {
    return detail.map(formatApiValidationIssue).join("; ");
  }
  if (detail && typeof detail === "object") {
    return JSON.stringify(detail);
  }
  return String(detail);
}

function formatApiValidationIssue(issue: unknown): string {
  if (!issue || typeof issue !== "object") {
    return String(issue);
  }
  const record = issue as Record<string, unknown>;
  const location = Array.isArray(record.loc) ? record.loc.join(".") : "";
  const message = typeof record.msg === "string" ? record.msg : JSON.stringify(record);
  return location ? `${location}: ${message}` : message;
}

export function getApiBase(): string {
  return API_BASE;
}
