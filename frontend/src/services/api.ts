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

export type ArtworkFeature = {
  bbox?: { width: number; height: number; min_x: number; min_y: number; max_x: number; max_y: number } | null;
  area: number;
  area_ratio: number;
  aspect_ratio: number;
  hole_count: number;
  concavity: number;
  parse_confidence: number;
  needs_manual_review: boolean;
  warnings: string[];
  metadata: Record<string, unknown>;
};

export type BatchArtworkItem = {
  item_id: string;
  batch_id: string;
  artwork_id?: string | null;
  filename: string;
  source_format: string;
  status: string;
  quantity: number;
  classification?: string | null;
  feature?: ArtworkFeature | null;
  parse_error?: string | null;
  retry_count: number;
  preflight_report?: {
    requires_conversion: boolean;
    requires_manual_review: boolean;
    can_parse_directly: boolean;
    warnings: string[];
  } | null;
};

export type BatchUpload = {
  batch_id: string;
  source_name?: string | null;
  status: string;
  item_count: number;
  uploaded_count: number;
  preflighted_count: number;
  parsed_count: number;
  conversion_required_count: number;
  manual_review_count: number;
  failed_count: number;
};

export type BatchArtworkSummary = {
  batch: BatchUpload;
  items: BatchArtworkItem[];
  class_counts: Record<string, number>;
  format_counts: Record<string, number>;
  status_counts: Record<string, number>;
};

export type SheetCutVariant = {
  variant_id: string;
  parent_id: string;
  code: string;
  kind: string;
  width: number;
  height: number;
  waste_rate: number;
  is_enabled: boolean;
};

export type BatchLayoutJob = {
  job_id: string;
  batch_id: string;
  status: string;
  moq_per_item: number;
  top_k: number;
  cut_variants: SheetCutVariant[];
};

export type BatchLayoutGroup = {
  group_id: string;
  job_id: string;
  compatibility_key: string;
  item_ids: string[];
  material?: string | null;
  thickness?: string | null;
  print_method?: string | null;
  spot_color?: string | null;
  stats: Record<string, unknown>;
};

export type ProductionPattern = {
  pattern_id: string;
  pattern_type: string;
  cut_variant_id?: string | null;
  units_per_sheet: number;
  required_sheets: number;
  total_units: number;
  utilization_rate: number;
  quantity_fulfillment_rate: number;
  hard_rule_pass: boolean;
  placement_json: Record<string, unknown>;
  placement_svg: string;
  placement_checksum?: string | null;
  placement_solver: Record<string, unknown>;
};

export type ProductionPlan = {
  plan_id: string;
  job_id: string;
  rank: number;
  intent: string;
  status: string;
  utilization_rate: number;
  risk_score: number;
  runtime_score: number;
  diversity_score: number;
  total_sheets_used: number;
  quantity_fulfillment_rate: number;
  hard_rule_pass: boolean;
  export_ok: boolean;
  validator_report: Record<string, unknown>;
  audit_manifest: Record<string, unknown>;
  patterns: ProductionPattern[];
};

export type ProductionPlanApproval = {
  id: string;
  plan_id: string;
  requested_by: string;
  decided_by?: string | null;
  status: "pending" | "approved" | "rejected";
  request_note?: string | null;
  decision_note?: string | null;
  snapshot: Record<string, unknown>;
};

export type BatchLayoutRunResult = {
  job: BatchLayoutJob;
  groups: BatchLayoutGroup[];
  plans: ProductionPlan[];
  summary: Record<string, unknown>;
};

export type BatchBenchmarkRun = {
  run_id: string;
  benchmark_type: string;
  status: string;
  file_count: number;
  p95_runtime_ms?: number | null;
  hard_rule_pass_rate: number;
  quantity_fulfillment_rate: number;
  topk_legal_rate: number;
  avg_case_score: number;
  metrics: Record<string, unknown>;
};

export async function uploadBatchArtworks(files: File[], sourceName: string): Promise<BatchArtworkSummary> {
  const form = new FormData();
  for (const file of files) {
    form.append("files", file);
  }
  if (sourceName) {
    form.append("source_name", sourceName);
  }
  form.append("metadata_json", JSON.stringify({ default_quantity: 1000 }));
  return apiRequest<BatchArtworkSummary>("/batch-artworks/upload", { method: "POST", body: form });
}

export function preflightBatchArtworks(batchId: string): Promise<BatchArtworkSummary> {
  return apiRequest<BatchArtworkSummary>(`/batch-artworks/${encodeURIComponent(batchId)}/preflight`, { method: "POST" });
}

export function parseBatchArtworks(batchId: string): Promise<BatchArtworkSummary> {
  return apiRequest<BatchArtworkSummary>(`/batch-artworks/${encodeURIComponent(batchId)}/parse`, { method: "POST" });
}

export function retryFailedBatchArtworks(batchId: string, itemIds?: string[]): Promise<BatchArtworkSummary> {
  return apiRequest<BatchArtworkSummary>(`/batch-artworks/${encodeURIComponent(batchId)}/retry-failed`, {
    method: "POST",
    body: JSON.stringify({ item_ids: itemIds && itemIds.length ? itemIds : null })
  });
}

export function createBatchLayoutJob(batchId: string): Promise<BatchLayoutJob> {
  return apiRequest<BatchLayoutJob>("/batch-layout/jobs", {
    method: "POST",
    body: JSON.stringify({ batch_id: batchId, moq_per_item: 1000, top_k: 3 })
  });
}

export function runBatchLayoutJob(jobId: string): Promise<BatchLayoutRunResult> {
  return apiRequest<BatchLayoutRunResult>(`/batch-layout/jobs/${encodeURIComponent(jobId)}/run`, { method: "POST" });
}

export function listBatchLayoutPlans(jobId: string): Promise<ProductionPlan[]> {
  return apiRequest<ProductionPlan[]>(`/batch-layout/jobs/${encodeURIComponent(jobId)}/plans`);
}

export function previewBatchPlan(planId: string): Promise<string> {
  return apiRequest<string>(`/batch-layout/plans/${encodeURIComponent(planId)}/preview`);
}

export function requestBatchPlanApproval(planId: string): Promise<ProductionPlanApproval> {
  return apiRequest<ProductionPlanApproval>(`/batch-layout/plans/${encodeURIComponent(planId)}/approval/request`, {
    method: "POST",
    body: JSON.stringify({ note: "Ready for production plan review" })
  });
}

export function approveBatchPlan(planId: string): Promise<ProductionPlanApproval> {
  return apiRequest<ProductionPlanApproval>(`/batch-layout/plans/${encodeURIComponent(planId)}/approval/decision`, {
    method: "POST",
    body: JSON.stringify({
      decision: "approved",
      note: "Approved for production plan export",
      confirmation: `APPROVE PLAN ${planId}`
    })
  });
}

export function exportBatchPlan(planId: string): Promise<Record<string, unknown>> {
  return apiRequest<Record<string, unknown>>(`/batch-layout/plans/${encodeURIComponent(planId)}/export`, {
    method: "POST",
    body: JSON.stringify({ confirmation: `EXPORT PLAN ${planId}` })
  });
}

export function runEnterpriseStress787(): Promise<BatchBenchmarkRun> {
  return apiRequest<BatchBenchmarkRun>("/benchmarks/run/stress-787", { method: "POST", body: JSON.stringify({}) });
}

export function runEnterpriseBatch1500(fileCount = 1500): Promise<BatchBenchmarkRun> {
  return apiRequest<BatchBenchmarkRun>("/benchmarks/run/batch-1500", {
    method: "POST",
    body: JSON.stringify({ file_count: fileCount })
  });
}

export function runEnterpriseBatch20000(fileCount = 20000): Promise<BatchBenchmarkRun> {
  return apiRequest<BatchBenchmarkRun>("/benchmarks/run/batch-20000", {
    method: "POST",
    body: JSON.stringify({ file_count: fileCount })
  });
}
