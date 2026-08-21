import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import Recorder from "../components/Recorder";
import { Empty, ErrorBox, MODE_LABEL, StatusPill, formatDuration } from "../components/common";
import { useAsync, useDocumentTitle } from "../hooks";
import type { Mode, Session } from "../types";

/** Home: pick up whatever is waiting to be recorded, or start a free-form session. */
export default function Practice({ onQueueChange }: { onQueueChange: () => void }) {
  useDocumentTitle("Practice");
  const awaiting = useAsync(() => api.sessions({ status: "awaiting_recording" }), []);
  const recorded = useAsync(() => api.sessions({ status: "recorded" }), []);
  const [active, setActive] = useState<Session | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [topic, setTopic] = useState("");
  const [creating, setCreating] = useState(false);
  const [creatingQuickMode, setCreatingQuickMode] = useState<Mode | null>(null);
  const [queueing, setQueueing] = useState<number | null>(null);

  const reloadAll = () => {
    awaiting.reload();
    recorded.reload();
    onQueueChange();
  };

  const startFreeform = async () => {
    setCreating(true);
    setError(null);
    try {
      const session = await api.createSession({ mode: "freeform", topic: topic.trim() || null });
      setTopic("");
      setActive(session);
      reloadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create the session.");
    } finally {
      setCreating(false);
    }
  };

  // worklog / brainstorm / journal: no topic or target words, the backend names the
  // session "<Mode> - <date>" itself.
  const startQuick = async (mode: Mode) => {
    setCreatingQuickMode(mode);
    setError(null);
    try {
      const session = await api.createSession({ mode });
      setActive(session);
      reloadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : `Could not create the ${mode} session.`);
    } finally {
      setCreatingQuickMode(null);
    }
  };

  // Transcription is a synchronous GPU run: a second click while one is in flight
  // starts a second WhisperX load on the same 6 GB card, so the button locks.
  const transcribeOnly = async (id: number) => {
    setQueueing(id);
    setError(null);
    try {
      await api.transcribe(id);
      reloadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not transcribe the session.");
    } finally {
      setQueueing(null);
    }
  };

  const queueForClaude = async (id: number) => {
    if (queueing !== null) return;
    setError(null);
    setQueueing(id);
    try {
      // The transcript already exists (the row only shows this button once it does) -
      // this step only flips the session to `pending`, it does not redo the GPU run.
      const result = await api.process(id, false, false);
      if (result.transcription_error) {
        setError(`Transcription failed: ${result.transcription_error}`);
      } else if (!result.queued) {
        setError(result.hint);
      }
      reloadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not queue the session.");
    } finally {
      setQueueing(null);
    }
  };

  const waiting = awaiting.data ?? [];
  const readyToProcess = recorded.data ?? [];

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Practice</h1>
          <p>
            Record against a topic, then queue it for Claude. Topics come from{" "}
            <code>/generate-topic</code> in the console.
          </p>
        </div>
      </div>

      <ErrorBox error={error} />

      {active && (
        <div className="card">
          <div className="card-head">
            <h2>
              {MODE_LABEL[active.mode as Mode]} · session {active.id}
            </h2>
            <button onClick={() => setActive(null)}>Close</button>
          </div>
          {active.topic && <p style={{ marginTop: 0 }}>{active.topic}</p>}
          {active.target_words.length > 0 && (
            <p className="small">
              Use these:{" "}
              {active.target_words.map((term) => (
                <span className="term" key={term}>
                  {term}
                </span>
              ))}
            </p>
          )}
          <Recorder
            sessionId={active.id}
            onUploaded={() => {
              setActive(null);
              reloadAll();
            }}
          />
        </div>
      )}

      <div className="card">
        <div className="card-head">
          <h2>Waiting to be recorded</h2>
          <span className="muted small">{waiting.length}</span>
        </div>
        {waiting.length === 0 ? (
          <Empty title="Nothing queued to record">
            <p>
              Run <code>/generate-topic</code> in the Claude Code console to get a topic
              with target vocabulary, or start a free-form session below.
            </p>
          </Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Topic</th>
                  <th>Mode</th>
                  <th>Target words</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {waiting.map((session) => (
                  <tr key={session.id}>
                    <td>
                      <div className="session-row-topic">{session.topic ?? "(no topic)"}</div>
                      <div className="session-row-meta">
                        {session.category ?? "no category"} · session {session.id}
                      </div>
                    </td>
                    <td className="small">{MODE_LABEL[session.mode]}</td>
                    <td>
                      {session.target_words.length === 0 ? (
                        <span className="muted small">—</span>
                      ) : (
                        session.target_words.map((term) => (
                          <span className="term" key={term}>
                            {term}
                          </span>
                        ))
                      )}
                    </td>
                    <td>
                      <button className="primary" onClick={() => setActive(session)}>
                        Record
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {readyToProcess.length > 0 && (
        <div className="card">
          <div className="card-head">
            <h2>Recorded, not yet queued</h2>
            <span className="muted small">{readyToProcess.length}</span>
          </div>
          <p className="card-sub">
            Transcribing runs on the GPU; the session is only queued for Claude once you
            say it&apos;s ready. <code>journal</code> sessions are never queued at all -
            transcribing is the whole process for them.
          </p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Topic</th>
                  <th>Length</th>
                  <th>Status</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {readyToProcess.map((session) => (
                  <tr key={session.id}>
                    <td>
                      <Link to={`/session/${session.id}`}>{session.topic ?? `Session ${session.id}`}</Link>
                      <div className="session-row-meta">{MODE_LABEL[session.mode]}</div>
                    </td>
                    <td className="num">{formatDuration(session.duration_sec)}</td>
                    <td>
                      <StatusPill status={session.status} />
                    </td>
                    <td>
                      {!session.has_transcript ? (
                        <button
                          className="primary"
                          onClick={() => transcribeOnly(session.id)}
                          disabled={queueing !== null}
                        >
                          {queueing === session.id ? "Transcribing…" : "Transcribe"}
                        </button>
                      ) : session.mode !== "journal" ? (
                        <button
                          className="primary"
                          onClick={() => queueForClaude(session.id)}
                          disabled={queueing !== null}
                        >
                          {queueing === session.id ? "Queuing…" : "Ready for AI processing"}
                        </button>
                      ) : (
                        <span className="muted small">Transcribed - nothing to queue</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="card">
        <div className="card-head">
          <h2>Free-form practice</h2>
        </div>
        <p className="card-sub">
          Your own topic, no target words. Leave it blank to just talk.
        </p>
        <div className="field">
          <label htmlFor="topic">Topic (optional)</label>
          <input
            id="topic"
            value={topic}
            placeholder="e.g. Explain our deployment process to a new joiner"
            onChange={(event) => setTopic(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void startFreeform();
            }}
          />
        </div>
        <div className="btn-row" style={{ marginTop: "0.85rem" }}>
          <button className="primary" onClick={startFreeform} disabled={creating}>
            {creating ? "Creating…" : "Start free-form session"}
          </button>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <h2>Daily worklog</h2>
        </div>
        <p className="card-sub">
          Ten minutes on your day: what you did, decisions and why, hurdles, wins,
          what&apos;s next. <code>/log-work</code> turns it into a journal entry — no score.
        </p>
        <div className="btn-row">
          <button
            className="primary"
            onClick={() => startQuick("worklog")}
            disabled={creatingQuickMode !== null}
          >
            {creatingQuickMode === "worklog" ? "Creating…" : "Record today's worklog"}
          </button>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <h2>Brainstorm</h2>
        </div>
        <p className="card-sub">
          Think out loud about anything - no structure needed.{" "}
          <code>/process-brainstorm</code> organises it into ideas — no coaching, no score.
        </p>
        <div className="btn-row">
          <button
            className="primary"
            onClick={() => startQuick("brainstorm")}
            disabled={creatingQuickMode !== null}
          >
            {creatingQuickMode === "brainstorm" ? "Creating…" : "Start a brainstorm"}
          </button>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <h2>Daily journal</h2>
        </div>
        <p className="card-sub">
          Family, goals, whatever&apos;s on your mind — off the record. Transcribed for
          your own reading, but never sent to Claude and never scored.
        </p>
        <div className="btn-row">
          <button
            className="primary"
            onClick={() => startQuick("journal")}
            disabled={creatingQuickMode !== null}
          >
            {creatingQuickMode === "journal" ? "Creating…" : "Record today's journal"}
          </button>
        </div>
      </div>
    </>
  );
}
