import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import Recorder, { type RecorderTarget } from "../components/Recorder";
import RelayBar from "../components/RelayBar";
import {
  Empty,
  ErrorBox,
  MODE_LABEL,
  StatusPill,
  formatDate,
  formatDuration,
} from "../components/common";
import { useAsync, useDocumentTitle, useInterval, useRelay } from "../hooks";
import type { Mode, RemoteMode, Session } from "../types";

/** The four modes that can be captured remotely - the ones that need no prior setup.
 *  `recommended` and `interview` carry target words chosen by `/generate-topic`, which
 *  is desk work by definition. */
const REMOTE_MODES: { mode: RemoteMode; title: string; blurb: string }[] = [
  {
    mode: "worklog",
    title: "Worklog",
    blurb: "What you did today, decisions and why, hurdles, wins. No score.",
  },
  {
    mode: "brainstorm",
    title: "Brainstorm",
    blurb: "Think out loud. Organised into ideas later - no coaching, no score.",
  },
  {
    mode: "journal",
    title: "Daily journal",
    blurb: "Off the record. Transcribed for you, never sent to Claude.",
  },
  {
    mode: "freeform",
    title: "Free-form practice",
    blurb: "Your own topic, no target words. Coached and scored.",
  },
];

/** Home: pick up whatever is waiting to be recorded, or start a free-form session. */
export default function Practice({ onQueueChange }: { onQueueChange: () => void }) {
  useDocumentTitle("Practice");
  const relay = useRelay();
  const awaiting = useAsync(() => api.sessions({ status: "awaiting_recording" }), []);
  const recorded = useAsync(() => api.sessions({ status: "recorded" }), []);
  const [active, setActive] = useState<Session | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [topic, setTopic] = useState("");
  const [creating, setCreating] = useState(false);
  const [creatingQuickMode, setCreatingQuickMode] = useState<Mode | null>(null);
  const [queueing, setQueueing] = useState<number | null>(null);

  // Remote capture: no session exists yet, so the recorder is opened against a mode
  // rather than an id, and the audio goes to the relay inbox (ADR 0006).
  const [capturing, setCapturing] = useState<RemoteMode | null>(null);
  const [captureTopic, setCaptureTopic] = useState("");
  const [sent, setSent] = useState<string | null>(null);

  const reloadAll = () => {
    awaiting.reload();
    recorded.reload();
    relay.refresh();
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

  // "Did it arrive?" has to be answerable from the phone that recorded it, not only
  // from the relay's log.
  const inbox = useAsync(
    async () => (relay.isRelay ? api.inboxRecent() : { items: [] }),
    [relay.isRelay],
  );
  const undrained = (inbox.data?.items ?? []).filter((item) => !item.acked_at);

  // Reaching the PC is what turns a capture into a session, so this is only worth
  // polling while something is actually in flight.
  useInterval(
    () => {
      inbox.reload();
      relay.refresh();
    },
    relay.isRelay && undrained.length > 0 ? 20000 : null,
  );

  // The desk-only cards create a session on the PC and then record into it - exactly
  // the dependency remote capture exists to remove. The PC-backed lists stay, but only
  // while the PC is actually up to serve them.
  const showDeskCards = !relay.isRelay;
  const showPcSections = !relay.isRelay || relay.pcOnline;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Practice</h1>
          <p>
            {relay.isRelay ? (
              "Record from anywhere. Your PC picks it up and processes it when it is next awake."
            ) : (
              <>
                Record against a topic, then queue it for Claude. Topics come from{" "}
                <code>/generate-topic</code> in the console.
              </>
            )}
          </p>
        </div>
      </div>

      <RelayBar status={relay.status} />
      <ErrorBox error={error} />

      {relay.isRelay && (
        <>
          {capturing ? (
            <div className="card">
              <div className="card-head">
                <h2>{REMOTE_MODES.find((m) => m.mode === capturing)?.title}</h2>
                <button onClick={() => setCapturing(null)}>Cancel</button>
              </div>
              {capturing === "freeform" && (
                <div className="field">
                  <label htmlFor="capture-topic">Topic (optional)</label>
                  <input
                    id="capture-topic"
                    value={captureTopic}
                    placeholder="e.g. Explain our deployment process"
                    onChange={(event) => setCaptureTopic(event.target.value)}
                  />
                </div>
              )}
              <Recorder
                target={
                  {
                    kind: "inbox",
                    mode: capturing,
                    topic: captureTopic.trim() || null,
                  } satisfies RecorderTarget
                }
                onUploaded={(result) => {
                  setCapturing(null);
                  setCaptureTopic("");
                  setSent(result?.hint ?? "Saved on the server.");
                  inbox.reload();
                  relay.refresh();
                }}
              />
            </div>
          ) : (
            <div className="card">
              <div className="card-head">
                <h2>Record</h2>
                {undrained.length > 0 && (
                  <span className="muted small">{undrained.length} waiting for your PC</span>
                )}
              </div>
              <p className="card-sub">
                Recordings are held on the server and become sessions the next time your
                PC is up - nothing is lost if it is asleep right now.
              </p>
              {sent && <p className="notice">{sent}</p>}
              <div className="capture-grid">
                {REMOTE_MODES.map(({ mode, title, blurb }) => (
                  <button
                    key={mode}
                    className="capture-tile"
                    onClick={() => {
                      setSent(null);
                      setCapturing(mode);
                    }}
                  >
                    <strong>{title}</strong>
                    <span className="small">{blurb}</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {(inbox.data?.items.length ?? 0) > 0 && (
            <div className="card">
              <div className="card-head">
                <h2>Sent from here</h2>
                <span className="muted small">{undrained.length} waiting</span>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Recorded</th>
                      <th>Mode</th>
                      <th>Size</th>
                      <th>State</th>
                    </tr>
                  </thead>
                  <tbody>
                    {inbox.data?.items.map((capture) => (
                      <tr key={capture.uid}>
                        <td className="small">{formatDate(capture.created_at)}</td>
                        <td className="small">{MODE_LABEL[capture.mode]}</td>
                        <td className="num">{(capture.bytes / 1e6).toFixed(1)} MB</td>
                        <td className="small">
                          {capture.acked_at && capture.session_id ? (
                            <Link to={`/session/${capture.session_id}`}>
                              session {capture.session_id}
                            </Link>
                          ) : capture.last_error ? (
                            <span className="muted" title={capture.last_error}>
                              retrying ({capture.attempts})
                            </span>
                          ) : (
                            <span className="muted">waiting for your PC</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}

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
            target={{ kind: "session", sessionId: active.id }}
            onUploaded={() => {
              setActive(null);
              reloadAll();
            }}
          />
        </div>
      )}

      {showPcSections && (
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
      )}

      {showPcSections && readyToProcess.length > 0 && (
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

      {showDeskCards && (
        <>
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
              <h2>Worklog</h2>
            </div>
            <p className="card-sub">
              Ten minutes on your day: what you did, decisions and why, hurdles, wins,
              what&apos;s next. Missed a day or two? Talk through all of them in one
              recording — <code>/log-work</code> splits it into one entry per day. No
              score.
            </p>
            <div className="btn-row">
              <button
                className="primary"
                onClick={() => startQuick("worklog")}
                disabled={creatingQuickMode !== null}
              >
                {creatingQuickMode === "worklog" ? "Creating…" : "Record worklog"}
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
      )}
    </>
  );
}
