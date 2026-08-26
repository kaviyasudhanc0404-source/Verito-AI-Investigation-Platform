/**
 * lib/api.ts
 * ----------
 * Centralised fetch wrapper for all VERITO backend API calls.
 * Automatically attaches the JWT Authorization header.
 */

import { getToken, clearToken } from "./auth-store";

export const API_BASE_URL =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000/api";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  isFormData?: boolean,
): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {};

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  if (body && !isFormData) {
    headers["Content-Type"] = "application/json";
  }

  const res = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers,
    body: isFormData
      ? (body as FormData)
      : body
        ? JSON.stringify(body)
        : undefined,
  });

  const isLoginAttempt =
    path === "/auth/login" || path === "/auth/signup" || path === "/auth/google";

  if ((res.status === 401 || res.status === 403) && !isLoginAttempt) {
    // Token expired or revoked - clear it
    clearToken();
    window.location.href = "/auth";
  }

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const err = await res.json();
      detail = err.detail ?? detail;
    } catch {}
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ── Auth ───────────────────────────────────────────────────────────────────────

export const authApi = {
  signup: (name: string, email: string, password: string) =>
    request<TokenResponse>("POST", "/auth/signup", { name, email, password }),

  login: (email: string, password: string) =>
    request<TokenResponse>("POST", "/auth/login", { email, password }),

  googleLogin: (id_token: string) =>
    request<TokenResponse>("POST", "/auth/google", { id_token }),

  me: () => request<UserResponse>("GET", "/auth/me"),

  updateProfile: (data: { name?: string; email?: string; current_password?: string; new_password?: string }) =>
    request<UserResponse>("PATCH", "/auth/me", data),
};

// ── Investigations ─────────────────────────────────────────────────────────────

export const investigationsApi = {
  list: () => request<InvestigationListItem[]>("GET", "/investigations"),

  create: (title: string, prompt: string) =>
    request<InvestigationResponse>("POST", "/investigations", { title, prompt }),

  get: (id: string) =>
    request<InvestigationResponse>("GET", `/investigations/${id}`),

  delete: (id: string) =>
    request<void>("DELETE", `/investigations/${id}`),

  update: (id: string, data: { title?: string; pinned?: boolean }) =>
    request<InvestigationListItem>("PATCH", `/investigations/${id}`, data),

  rename: (id: string, title: string) =>
    request<InvestigationListItem>("PATCH", `/investigations/${id}`, { title }),
};

// ── Evidence ───────────────────────────────────────────────────────────────────

export const evidenceApi = {
  upload: (investigationId: string, files: File[]): Promise<EvidenceItemResponse[]> => {
    const formData = new FormData();
    files.forEach((f) => formData.append("files", f));
    return request<EvidenceItemResponse[]>(
      "POST",
      `/evidence/upload/${investigationId}`,
      formData,
      true,
    );
  },

  list: (investigationId: string) =>
    request<EvidenceItemResponse[]>("GET", `/evidence/${investigationId}`),
};

// ── Chat ───────────────────────────────────────────────────────────────────────

export const chatApi = {
  send: (investigationId: string, text: string) =>
    request<ChatMessageResponse[]>("POST", `/chat/${investigationId}`, { text }),

  history: (investigationId: string) =>
    request<ChatMessageResponse[]>("GET", `/chat/${investigationId}`),
};

// ── Types (mirroring backend schemas) ─────────────────────────────────────────

export interface TokenResponse {
  access_token: string;
  token_type: string;
  user_id: string;
  name: string;
  email: string;
}

export interface UserResponse {
  user_id: string;
  name: string;
  email: string;
}

export interface InvestigationListItem {
  id: string;
  title: string;
  status: "processing" | "complete" | "failed";
  pinned: boolean;
  created_at: string;
}

export interface InvestigationResponse {
  id: string;
  title: string;
  prompt: string;
  status: "processing" | "complete" | "failed";
  pinned: boolean;
  created_at: string;
  evidence: EvidenceItemResponse[];
  runs: AgentRunResponse[];
  report?: ReportResponse;
  chat: ChatMessageResponse[];
}

export interface EvidenceItemResponse {
  id: string;
  name: string;
  kind: "doc" | "image" | "audio" | "text" | "chat" | "map" | "video";
  size?: number;
}

export interface AgentRunResponse {
  id: string;
  status: "idle" | "running" | "done" | "completed_with_warnings" | "failed" | "blocked";
  lines: { kind: string; text: string }[];
  summary: string[];
}

export interface ReportResponse {
  summary: string;
  findings: string[];
  timeline: { time: string; event: string; refs: string[] }[];
  connections: { a: string; b: string; reason: string }[];
  persons: { name: string; note: string }[];
  unresolved: string[];
}

export interface ChatMessageResponse {
  id: string;
  role: "user" | "verito";
  text: string;
  refs: string[];
}
