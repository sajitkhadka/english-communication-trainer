export type Mode =
  | "recommended"
  | "freeform"
  | "interview"
  | "worklog"
  | "brainstorm"
  | "journal";
export type SessionStatus = "awaiting_recording" | "recorded" | "pending" | "processed";
/** Modes `change_session_mode` accepts as a switch target (backend: SWITCHABLE_MODES). */
export type SwitchableMode = "freeform" | "worklog" | "brainstorm" | "journal";

export interface Score {
  id: number;
  session_id: number;
  vocab_range: number | null;
  filler_density: number | null;
  fluency: number | null;
  grammar: number | null;
  structure: number | null;
  coherence: number | null;
  target_usage: number | null;
  overall: number | null;
  created_at: string | null;
}

export interface Session {
  id: number;
  mode: Mode;
  category: string | null;
  topic: string | null;
  target_words: string[];
  status: SessionStatus;
  audio_path: string | null;
  transcript_path: string | null;
  feedback_path: string | null;
  created_at: string | null;
  processed_at: string | null;
  transcribe_status: string | null;
  transcribe_error: string | null;
  duration_sec: number | null;
  notes: string | null;
  title: string | null;
  summary: string | null;
  /** Set when the recording arrived over the relay rather than being made here. */
  external_uid: string | null;
  score: Score | null;
  has_audio: boolean;
  has_transcript: boolean;
  has_feedback: boolean;
}

export interface TargetWordDetail {
  term: string;
  kind?: string | null;
  meaning?: string | null;
  example?: string | null;
  mastery?: number | null;
  due_date?: string | null;
}

export interface SessionDetail extends Session {
  feedback_markdown: string | null;
  target_words_detail: TargetWordDetail[];
}

export interface Word {
  id: number;
  term: string;
  kind: string | null;
  meaning: string | null;
  example: string | null;
  first_seen: string | null;
  last_practiced: string | null;
  times_seen: number;
  times_used_correctly: number;
  ease: number;
  interval_days: number;
  due_date: string | null;
  mastery: number;
  source: string | null;
  repetitions: number;
  notes: string | null;
  is_due: boolean;
}

export interface WordStats {
  total: number;
  due: number;
  mastered: number;
  never_practiced: number;
  avg_mastery: number;
}

export interface Suggestion {
  id: number;
  mode: Mode;
  category: string | null;
  topic: string;
  target_words: string[];
  rationale: string | null;
  created_at: string | null;
  consumed_by: number | null;
}

export interface ProgressPayload {
  history: (Score & { mode: Mode; topic: string | null; category: string | null })[];
  sessions_scored: number;
  latest: number | null;
  best: number | null;
  average: number | null;
  recent_average: number | null;
  delta_vs_earlier: number | null;
  vocabulary: WordStats;
  dimensions: string[];
  weights: Record<string, number>;
}

/** Shape of data/transcripts/<id>.json — see pipeline/runner.py:build_payload. */
export interface Transcript {
  session_id: number;
  mode: Mode;
  category: string | null;
  topic: string | null;
  target_words: string[];
  audio: { file: string; duration_sec: number; sample_rate: number };
  transcript: {
    text: string;
    sentences: {
      i: number;
      text: string;
      start: number | null;
      end: number | null;
      words: number;
      wpm: number | null;
    }[];
    words: { w: string; s: number | null; e: number | null }[];
  };
  speech: {
    words_total: number;
    wpm_overall: number | null;
    wpm_speaking: number | null;
    speaking_sec: number;
    silence_sec: number;
    speech_ratio: number | null;
    sentence_count: number;
    avg_sentence_words: number | null;
  };
  pauses: {
    count: number;
    per_minute: number | null;
    total_sec: number;
    longest_sec: number;
    mid_sentence_count: number;
    buckets: Record<string, number>;
    items: {
      start: number;
      dur: number;
      after_word: string | null;
      sentence: number | null;
      mid_sentence: boolean;
    }[];
  };
  fillers: {
    textual: {
      total: number;
      hard_total: number;
      ambiguous_total: number;
      per_minute: number | null;
      by_term: Record<string, number>;
      items: { term: string; start: number | null; ambiguous: boolean; word_index: number }[];
    };
    acoustic: {
      total: number;
      per_minute: number | null;
      note: string;
      items: {
        start: number;
        dur: number;
        word_coverage: number;
        after_word: string | null;
        sentence: number | null;
        heard_as: string | null;
      }[];
    };
    combined_total: number;
    combined_per_minute: number | null;
    // Absent on transcripts written before the Parakeet cross-check pass was added -
    // never assume it's there.
    cross_check?: {
      total: number;
      per_minute: number | null;
      note: string;
      items: { term: string; start: number; sentence: number | null }[];
    };
  };
  target_word_hits: {
    term: string;
    found: boolean;
    count: number;
    occurrences: { start: number | null; said_as: string; sentence: number | null }[];
    confirmed_by: string[];
  }[];
  meta: {
    pipeline_version: number;
    model: string;
    compute_type: string;
    aligned: boolean;
    language: string;
    vad: string;
    target_word_cross_check: string | null;
    elapsed_sec: number;
    generated_at: string;
    thresholds: Record<string, number>;
  };
}

/** `data/learning-notes.md` — the durable coaching wiki, shared with /process-session. */
export interface Notes {
  markdown: string;
  path: string | null;
  /** Opaque. Hand it back on save so a concurrent skill edit is caught, not overwritten. */
  version: string;
}

export interface QueuePayload {
  pending: Session[];
  count: number;
  hint: string;
}

export interface ProcessResponse {
  session_id: number;
  status: string;
  /** False when transcription failed - the session stays `recorded`, not queued. */
  queued: boolean;
  transcription?: Record<string, unknown> | null;
  transcription_error?: string | null;
  hint: string;
}

export interface Health {
  ok: boolean;
  db: string;
  model: string;
  compute_type: string;
  device: string;
  pending_sessions: number;
  sessions_by_mode: Record<Mode, number>;
}

// --- the relay (docs/adr/0006-remote-capture-local-processing.md) ---

/** Modes that can be captured remotely: the four that need no prior setup. The coached
 *  modes carry target words chosen by `/generate-topic`, which is desk work. */
export type RemoteMode = "freeform" | "worklog" | "brainstorm" | "journal";

/** `GET /api/relay/status`, answered only by the relay - the PC's API 404s it, which is
 *  exactly how the app tells which side it is being served from. */
export interface RelayStatus {
  relay: true;
  pc_online: boolean;
  pc: {
    online: boolean;
    api_ok: boolean;
    proxy_failed: boolean;
    ttl_seconds: number;
    last_heartbeat: string | null;
    seconds_since_heartbeat: number | null;
  };
  inbox_pending: number;
  digest_version: string | null;
  digest_at: string | null;
  digest_sessions: number;
  modes: RemoteMode[];
}

/** One capture in the relay inbox, on its way to the PC. */
export interface InboxItem {
  uid: string;
  mode: RemoteMode;
  topic?: string;
  bytes: number;
  created_at: string;
  acked_at?: string;
  session_id?: number | null;
  attempts: number;
  last_error?: string;
}

export interface InboxUploadResult {
  uid: string;
  mode: RemoteMode;
  bytes: number;
  created_at: string;
  inbox_pending: number;
  pc_online: boolean;
  hint: string;
}
