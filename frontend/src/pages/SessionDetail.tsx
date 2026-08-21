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
  feedbackCardTitle,
  formatDate,
  formatDuration,
  processCommand,
} from "../components/common";
import { useAsync, useDocumentTitle, useRefreshOnFocus } from "../hooks";
import type { Score, SwitchableMode } from "../types";

const SWITCHABLE_MODES: SwitchableMode[] = ["freeform", "worklog", "brainstorm", "journal"];

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
  const [switchingMode, setSwitchingMode] = useState(false);

  useDocumentTitle(session.data?.topic ?? `Session ${sessionId}`);

  // Feedback arrives while the user is in the Claude Code console, not in the browser,
  // so refetch on return rather than making them reload the page by hand.
  useRefreshOnFocus(() => {
    session.reload();
    transcript.reload();
    onQueueChange();
  });

  const data = session.data;

  const transcribeOnly = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.transcribe(sessionId);
      session.reload();
      transcript.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not transcribe the session.");
    } finally {
      setBusy(false);
    }
  };

  const queueForClaude = async (force = false, transcribeFlag?: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.process(sessionId, force, transcribeFlag);
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

  const changeMode = async (mode: SwitchableMode) => {
    setSwitchingMode(true);
    setError(null);
    try {
      await api.setMode(sessionId, mode);
      session.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change the session's mode.");
    } finally {
      setSwitchingMode(false);
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
          <h1>{data.title ?? data.topic ?? `Session ${data.id}`}</h1>
          {data.summary && (
            <p className="small muted" style={{ marginTop: "-0.4rem" }}>
              {data.summary}
            </p>
          )}
          <p>
            {data.category && <>{data.category} · </>}
            {formatDuration(data.duration_sec)}
          </p>
          {data.status !== "processed" && SWITCHABLE_MODES.includes(data.mode as SwitchableMode) && (
            <p className="small muted">
              Not sure this was the right kind of recording?{" "}
              <select
                value={data.mode}
                disabled={switchingMode}
                onChange={(event) => void changeMode(event.target.value as SwitchableMode)}
              >
                {SWITCHABLE_MODES.map((mode) => (
                  <option key={mode} value={mode}>
                    {MODE_LABEL[mode]}
                  </option>
                ))}
              </select>
            </p>
          )}
        </div>
        <div className="btn-row">
          <StatusPill status={data.status} />
          {data.status === "recorded" && !data.has_transcript && (
            <button className="primary" onClick={transcribeOnly} disabled={busy}>
              {busy ? "Transcribing…" : "Transcribe"}
            </button>
          )}
          {data.status === "recorded" && data.has_transcript && (
            <button
              className="primary"
              onClick={() => queueForClaude(false, false)}
              disabled={busy}
            >
              {busy ? "Queuing…" : "Ready for AI processing"}
            </button>
          )}
          {data.status === "pending" && (
            <button onClick={() => queueForClaude(true)} disabled={busy}>
              {busy ? "Transcribing…" : "Process again"}
            </button>
          )}
          {data.status === "processed" && data.mode !== "journal" && (
            <button onClick={() => queueForClaude(true)} disabled={busy}>
              {busy ? "Transcribing…" : "Re-queue"}
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
          Queued for Claude. Run <code>{processCommand(data.mode) ?? "/process-session"}</code>{" "}
          in the Claude Code console to
          {data.mode === "worklog"
            ? " turn this recording into a journal entry."
            : data.mode === "brainstorm"
              ? " organise this into ideas."
              : " generate feedback for every queued session."}
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
                        {hit ? (
                          hit.found ? (
                            hit.confirmed_by.includes("whisper") ? (
                              `yes (${hit.count}×)`
                            ) : (
                              <span title="Heard by a second speech-recognition pass, but not in the main transcript - unverified.">
                                yes (unverified)
                              </span>
                            )
                          ) : (
                            "no"
                          )
                        ) : (
                          "—"
                        )}
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
            Not transcribed yet — press Transcribe above to run the analysis.
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
            <h2>{feedbackCardTitle(data.mode)}</h2>
            <span className="muted small">{data.feedback_path}</span>
          </div>
          <Markdown>{data.feedback_markdown}</Markdown>
        </div>
      ) : (
        data.status !== "awaiting_recording" && (
          <div className="card">
            <Empty title={`No ${feedbackCardTitle(data.mode).toLowerCase()} yet`}>
              <p>
                {data.mode === "journal" ? (
                  <>
                    Press <strong>Transcribe</strong> above — the transcript becomes this
                    entry automatically, no console command needed.
                  </>
                ) : data.status === "pending" ? (
                  <>
                    This session is queued. Run <code>{processCommand(data.mode)}</code> in the
                    console.
                  </>
                ) : (
                  <>
                    Press <strong>Transcribe</strong> above, then{" "}
                    <strong>Ready for AI processing</strong>, then run{" "}
                    <code>{processCommand(data.mode)}</code> in the console.
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
