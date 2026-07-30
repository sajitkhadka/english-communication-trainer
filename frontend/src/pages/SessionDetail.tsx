import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api } from "../api";
import { ScoreBars } from "../components/Charts";
import Playback from "../components/Playback";
import {
  DIMENSIONS,
  Empty,
  ErrorBox,
  MODE_LABEL,
  Markdown,
  StatusPill,
  Tile,
  formatDate,
  formatDuration,
} from "../components/common";
import { useAsync, useDocumentTitle } from "../hooks";
import type { Score } from "../types";

export default function SessionDetail({ onQueueChange }: { onQueueChange: () => void }) {
  const { id } = useParams();
  const sessionId = Number(id);
  const navigate = useNavigate();

  const session = useAsync(() => api.session(sessionId), [sessionId]);
  const transcript = useAsync(
    () => api.transcript(sessionId).catch(() => null),
    [sessionId, session.data?.has_transcript],
  );

  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useDocumentTitle(session.data?.topic ?? `Session ${sessionId}`);

  const data = session.data;

  const queueForClaude = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.process(sessionId);
      if (result.transcription_error) {
        setError(`Transcription failed: ${result.transcription_error}`);
      } else if (!result.queued) {
        setError(result.hint);
      }
      session.reload();
      transcript.reload();
      onQueueChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not queue the session.");
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!window.confirm(`Delete session ${sessionId} and its recording, transcript and feedback?`)) {
      return;
    }
    try {
      await api.deleteSession(sessionId);
      onQueueChange();
      navigate("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete the session.");
    }
  };

  if (session.loading && !data) return <p className="muted">Loading…</p>;
  if (session.error) return <p className="error">{session.error}</p>;
  if (!data) return null;

  const score = data.score;

  return (
    <>
      <div className="page-head">
        <div>
          <p className="small muted" style={{ margin: 0 }}>
            <Link to={`/${data.mode}`}>{MODE_LABEL[data.mode]}</Link> · session {data.id} ·{" "}
            {formatDate(data.created_at)}
          </p>
          <h1>{data.topic ?? `Session ${data.id}`}</h1>
          <p>
            {data.category && <>{data.category} · </>}
            {formatDuration(data.duration_sec)}
          </p>
        </div>
        <div className="btn-row">
          <StatusPill status={data.status} />
          {(data.status === "recorded" || data.status === "processed") && (
            <button className="primary" onClick={queueForClaude} disabled={busy}>
              {busy ? "Transcribing…" : data.status === "processed" ? "Re-queue" : "Process"}
            </button>
          )}
          <button className="danger" onClick={remove}>
            Delete
          </button>
        </div>
      </div>

      <ErrorBox error={error} />

      {data.status === "pending" && (
        <p className="notice">
          Queued for Claude. Run <code>/process-session</code> in the Claude Code console to
          generate feedback for every queued session.
        </p>
      )}
      {data.transcribe_status === "error" && (
        <p className="error">Transcription failed: {data.transcribe_error}</p>
      )}

      {data.target_words_detail.length > 0 && (
        <div className="card">
          <div className="card-head">
            <h2>Target vocabulary</h2>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Term</th>
                  <th>Kind</th>
                  <th>Meaning</th>
                  <th>Used</th>
                </tr>
              </thead>
              <tbody>
                {data.target_words_detail.map((word) => {
                  const hit = transcript.data?.target_word_hits.find((h) => h.term === word.term);
                  return (
                    <tr key={word.term}>
                      <td>
                        <span className={`term ${hit ? (hit.found ? "used" : "missed") : ""}`}>
                          {word.term}
                        </span>
                      </td>
                      <td className="small muted">{word.kind ?? "—"}</td>
                      <td className="small">{word.meaning ?? "—"}</td>
                      <td className="small">
                        {hit ? (hit.found ? `yes (${hit.count}×)` : "no") : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {transcript.data ? (
        <>
          <div className="card">
            <div className="card-head">
              <h2>Delivery</h2>
              <span className="muted small">
                {transcript.data.meta.model} @ {transcript.data.meta.compute_type}
              </span>
            </div>
            <div className="tiles">
              <Tile
                label="Pace"
                value={transcript.data.speech.wpm_overall ?? "—"}
                sub="words per minute"
              />
              <Tile
                label="Fillers"
                value={transcript.data.fillers.combined_per_minute ?? "—"}
                sub={`per minute · ${transcript.data.fillers.combined_total} total`}
              />
              <Tile
                label="Pauses"
                value={transcript.data.pauses.count}
                sub={`longest ${transcript.data.pauses.longest_sec}s · ${transcript.data.pauses.mid_sentence_count} mid-sentence`}
              />
              <Tile
                label="Speaking"
                value={`${Math.round((transcript.data.speech.speech_ratio ?? 0) * 100)}%`}
                sub={`${transcript.data.speech.words_total} words`}
              />
            </div>
          </div>

          <div className="card">
            <div className="card-head">
              <h2>Recording</h2>
            </div>
            <Playback sessionId={sessionId} transcript={transcript.data} />
          </div>
        </>
      ) : data.has_audio ? (
        <div className="card">
          <div className="card-head">
            <h2>Recording</h2>
          </div>
          <audio src={api.audioUrl(sessionId)} controls style={{ width: "100%" }} />
          <p className="muted small" style={{ marginBottom: 0 }}>
            Not transcribed yet — press Process to run the analysis.
          </p>
        </div>
      ) : (
        <div className="card">
          <Empty title="No recording yet">
            <p>
              <Link to="/">Go to Practice</Link> to record this session.
            </p>
          </Empty>
        </div>
      )}

      {score && (
        <div className="card">
          <div className="card-head">
            <h2>Score</h2>
            <span className="tnum" style={{ fontSize: "1.4rem", fontWeight: 680 }}>
              {score.overall?.toFixed(1) ?? "—"}
              <span className="muted small"> / 10 overall</span>
            </span>
          </div>
          <ScoreBars
            rows={DIMENSIONS.map((dimension) => ({
              label: dimension.label,
              value: (score[dimension.key as keyof Score] as number | null) ?? null,
            }))}
          />
        </div>
      )}

      {data.feedback_markdown ? (
        <div className="card">
          <div className="card-head">
            <h2>Feedback</h2>
            <span className="muted small">{data.feedback_path}</span>
          </div>
          <Markdown>{data.feedback_markdown}</Markdown>
        </div>
      ) : (
        data.status !== "awaiting_recording" && (
          <div className="card">
            <Empty title="No feedback yet">
              <p>
                {data.status === "pending" ? (
                  <>
                    This session is queued. Run <code>/process-session</code> in the console.
                  </>
                ) : (
                  <>
                    Press <strong>Process</strong> above, then run{" "}
                    <code>/process-session</code> in the console.
                  </>
                )}
              </p>
            </Empty>
          </div>
        )
      )}
    </>
  );
}
