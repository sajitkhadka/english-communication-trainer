import type {
  Health,
  Mode,
  ProcessResponse,
  ProgressPayload,
  QueuePayload,
  Session,
  SessionDetail,
  Suggestion,
  SwitchableMode,
  Transcript,
  Word,
  WordStats,
} from "./types";

const BASE = "/api";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, init);
  if (!response.ok) {
    // FastAPI puts the useful part in `detail`; fall back to the status text.
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") detail = body.detail;
      else if (Array.isArray(body?.detail)) detail = JSON.stringify(body.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(detail, response.status);
  }
  if (response.status === 204) return undefined as T;
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) return (await response.json()) as T;
  return (await response.text()) as unknown as T;
}

export const api = {
  health: () => request<Health>("/health"),
  doctor: () => request<Record<string, unknown>>("/doctor"),

  sessions: (params: { mode?: Mode; status?: string } = {}) => {
    const query = new URLSearchParams();
    if (params.mode) query.set("mode", params.mode);
    if (params.status) query.set("status", params.status);
    const suffix = query.toString() ? `?${query}` : "";
    return request<Session[]>(`/sessions${suffix}`);
  },

  session: (id: number) => request<SessionDetail>(`/sessions/${id}`),

  createSession: (body: {
    mode: Mode;
    topic?: string | null;
    category?: string | null;
    target_words?: string[];
    notes?: string | null;
  }) =>
    request<Session>("/sessions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }),

  deleteSession: (id: number) => request<void>(`/sessions/${id}`, { method: "DELETE" }),

  uploadRecording: (id: number, blob: Blob, filename: string) => {
    const form = new FormData();
    form.append("file", blob, filename);
    return request<Session>(`/sessions/${id}/recording`, { method: "POST", body: form });
  },

  transcript: (id: number) => request<Transcript>(`/sessions/${id}/transcript`),
  feedback: (id: number) => request<string>(`/sessions/${id}/feedback`),
  brief: (id: number) => request<string>(`/sessions/${id}/brief`),
  audioUrl: (id: number) => `${BASE}/sessions/${id}/audio`,

  // `transcribe: false` is the "ready for AI processing" step: the session already
  // has a transcript, and this only flips it to `pending` without re-running the GPU
  // pipeline. Omit it (default) to transcribe-and-queue in one call, e.g. "Process again".
  process: (id: number, force = false, transcribe?: boolean) => {
    const query = new URLSearchParams();
    if (force) query.set("force", "true");
    if (transcribe !== undefined) query.set("transcribe", String(transcribe));
    const suffix = query.toString() ? `?${query}` : "";
    return request<ProcessResponse>(`/sessions/${id}/process${suffix}`, { method: "POST" });
  },
  transcribe: (id: number) =>
    request<Record<string, unknown>>(`/sessions/${id}/transcribe`, { method: "POST" }),
  setMode: (id: number, mode: SwitchableMode) =>
    request<Session>(`/sessions/${id}/mode`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ mode }),
    }),

  queue: () => request<QueuePayload>("/queue"),

  words: (sort = "recency") => request<Word[]>(`/words?sort=${sort}`),
  dueWords: (limit = 15) => request<Word[]>(`/words/due?limit=${limit}`),
  wordStats: () => request<WordStats>("/words/stats"),

  progress: () => request<ProgressPayload>("/progress"),

  suggestions: () => request<Suggestion[]>("/suggestions"),
  requestSuggestions: (body: { mode: Mode; category?: string | null }) =>
    request<{ id: number; status: string; hint: string }>("/suggestions/requests", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }),
  useSuggestion: (sessionId: number, suggestionId: number) =>
    request<{ suggestion_id: number; consumed_by: number }>(
      `/sessions/${sessionId}/use-suggestion/${suggestionId}`,
      { method: "POST" },
    ),
};
