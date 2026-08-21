import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import {
  Empty,
  ErrorBox,
  MODE_LABEL,
  StatusPill,
  formatDate,
  formatDuration,
} from "../components/common";
import { useAsync, useDocumentTitle } from "../hooks";
import type { Mode } from "../types";

// Modes that are never coached/scored - a recording, not a practice repetition.
const UNSCORED_MODES: Mode[] = ["freeform", "worklog", "brainstorm", "journal"];

const BLURB: Record<Mode, string> = {
  recommended: "Topics Claude generated with target vocabulary to work in.",
  freeform: "Your own topics. No target words, so target-word usage is not scored.",
  interview: "One-way interview practice: a question, one answer, the same analysis.",
  worklog:
    "Daily work journal: talk through your day, then /log-work turns it into a structured entry. Not scored.",
  brainstorm:
    "Think out loud about anything. /process-brainstorm organises it into ideas - no coaching, no score.",
  journal:
    "Daily life, off the record. Transcribed for your own reading only - never sent to Claude, never scored.",
};

export default function Sessions({
  mode,
  onQueueChange,
}: {
  mode: Mode;
  onQueueChange: () => void;
}) {
  useDocumentTitle(MODE_LABEL[mode]);
  const sessions = useAsync(() => api.sessions({ mode }), [mode]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  const transcribeOnly = async (id: number) => {
    if (busy !== null) return;
    setBusy(id);
    setError(null);
    try {
      await api.transcribe(id);
      sessions.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not transcribe the session.");
    } finally {
      setBusy(null);
    }
  };

  const queueForClaude = async (id: number, force = false, transcribe?: boolean) => {
    if (busy !== null) return;
    setBusy(id);
    setError(null);
    try {
      const result = await api.process(id, force, transcribe);
      if (result.transcription_error) {
        setError(`Transcription failed: ${result.transcription_error}`);
      } else if (!result.queued) {
        setError(result.hint);
      }
      sessions.reload();
      onQueueChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not queue the session.");
    } finally {
      setBusy(null);
    }
  };

  const rows = sessions.data ?? [];

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{MODE_LABEL[mode]}</h1>
          <p>{BLURB[mode]}</p>
        </div>
      </div>

      <ErrorBox error={error ?? sessions.error} />

      <div className="card">
        {sessions.loading && rows.length === 0 ? (
          <p className="muted">Loading…</p>
        ) : rows.length === 0 ? (
          <Empty title={`No ${MODE_LABEL[mode].toLowerCase()} sessions yet`}>
            <p>
              {UNSCORED_MODES.includes(mode) ? (
                <>
                  Start one from the <Link to="/">Practice</Link> page.
                </>
              ) : (
                <>
                  Run <code>/generate-topic {mode === "interview" ? "interview" : ""}</code> in
                  the Claude Code console.
                </>
              )}
            </p>
          </Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Topic</th>
                  <th>Date</th>
                  <th className="num">Length</th>
                  <th className="num">Score</th>
                  <th>Status</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((session) => (
                  <tr key={session.id}>
                    <td>
                      <Link to={`/session/${session.id}`} className="session-row-topic">
                        {session.title ?? session.topic ?? `Session ${session.id}`}
                      </Link>
                      <div className="session-row-meta">
                        {session.summary ? (
                          session.summary
                        ) : (
                          <>
                            {session.category && <>{session.category} · </>}
                            {session.target_words.length > 0
                              ? `${session.target_words.length} target words`
                              : "no target words"}
                          </>
                        )}
                      </div>
                    </td>
                    <td className="small">{formatDate(session.created_at)}</td>
                    <td className="num">{formatDuration(session.duration_sec)}</td>
                    <td className="num">
                      {session.score?.overall != null ? (
                        <strong>{session.score.overall.toFixed(1)}</strong>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td>
                      <StatusPill status={session.status} />
                    </td>
                    <td>
                      {session.status === "recorded" && !session.has_transcript && (
                        <button
                          className="primary"
                          onClick={() => transcribeOnly(session.id)}
                          disabled={busy !== null}
                        >
                          {busy === session.id ? "Transcribing…" : "Transcribe"}
                        </button>
                      )}
                      {session.status === "recorded" && session.has_transcript && (
                        <button
                          className="primary"
                          onClick={() => queueForClaude(session.id, false, false)}
                          disabled={busy !== null}
                        >
                          {busy === session.id ? "Queuing…" : "Ready for AI processing"}
                        </button>
                      )}
                      {session.status === "pending" && (
                        <button
                          onClick={() => queueForClaude(session.id, true)}
                          disabled={busy !== null}
                        >
                          {busy === session.id ? "Transcribing…" : "Process again"}
                        </button>
                      )}
                      {session.status === "awaiting_recording" && (
                        <Link className="btn" to="/">
                          Record
                        </Link>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
