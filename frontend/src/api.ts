import type {
  Health,
  InboxItem,
  InboxUploadResult,
  Mode,
  Notes,
  ProcessResponse,
  ProgressPayload,
  QueuePayload,
  RelayStatus,
  RemoteMode,
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

  notes: () => request<Notes>("/notes"),
  // `version` is what this editor loaded; the backend 409s rather than overwriting a
  // newer one (a /process-session run edits the same file).
  saveNotes: (markdown: string, version: string) =>
    request<Notes>("/notes", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ markdown, version }),
    }),

  words: (sort = "recency") => request<Word[]>(`/words?sort=${sort}`),
  dueWords: (limit = 15) => request<Word[]>(`/words/due?limit=${limit}`),
  wordStats: () => request<WordStats>("/words/stats"),

  progress: () => request<ProgressPayload>("/progress"),

  suggestions: () => request<Suggestion[]>("/suggestions"),

  // --- the relay (ADR 0006) ---

  // Only the relay answers this; the PC's API has no such route and 404s, which is how
  // the app knows which side is serving it without a second build or a build-time flag.
  relayStatus: () => request<RelayStatus>("/relay/status"),

  // Send a capture straight to the relay's inbox. Deliberately not a session: with the
  // PC asleep there is nothing to create one against, and one SQLite file stays the
  // source of truth. `ect agent` turns this into a session later, using `uid` as
  // `external_uid` so a retried upload and a repeated drain collapse into one session.
  // Field order matters - the relay streams the file part straight to disk, so the
  // fields describing it have to be appended first.
  uploadToInbox: (body: {
    uid: string;
    mode: RemoteMode;
    topic?: string | null;
    notes?: string | null;
    blob: Blob;
    filename: string;
  }) => {
    const form = new FormData();
    form.append("uid", body.uid);
    form.append("mode", body.mode);
    if (body.topic) form.append("topic", body.topic);
    if (body.notes) form.append("notes", body.notes);
    form.append("file", body.blob, body.filename);
    return request<InboxUploadResult>("/inbox", { method: "POST", body: form });
  },

  inboxRecent: () => request<{ items: InboxItem[] }>("/inbox/recent"),
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
