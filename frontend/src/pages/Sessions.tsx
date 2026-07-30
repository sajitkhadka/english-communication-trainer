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

const BLURB: Record<Mode, string> = {
  recommended: "Topics Claude generated with target vocabulary to work in.",
  freeform: "Your own topics. No target words, so target-word usage is not scored.",
  interview: "One-way interview practice: a question, one answer, the same analysis.",
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

  const queueForClaude = async (id: number) => {
    if (busy !== null) return;
    setBusy(id);
    setError(null);
    try {
      const result = await api.process(id);
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
              {mode === "freeform" ? (
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
                        {session.topic ?? `Session ${session.id}`}
                      </Link>
                      <div className="session-row-meta">
                        {session.category && <>{session.category} · </>}
                        {session.target_words.length > 0
                          ? `${session.target_words.length} target words`
                          : "no target words"}
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
                      {session.status === "recorded" && (
                        <button
                          className="primary"
                          onClick={() => queueForClaude(session.id)}
                          disabled={busy !== null}
                        >
                          {busy === session.id ? "Transcribing…" : "Process"}
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
