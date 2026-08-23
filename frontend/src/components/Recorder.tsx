import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../api";
import { formatClock } from "./common";

type Phase = "idle" | "recording" | "paused" | "review" | "uploading";

/** Pick a container the browser can actually produce. ffmpeg decodes all of these
 *  backend-side, so we just take the first supported one. */
function pickMimeType(): string | undefined {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
    "audio/mp4",
  ];
  if (typeof MediaRecorder === "undefined") return undefined;
  return candidates.find((type) => MediaRecorder.isTypeSupported(type));
}

export default function Recorder({
  sessionId,
  onUploaded,
}: {
  sessionId: number;
  onUploaded: () => void;
}) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [elapsed, setElapsed] = useState(0);
  const [level, setLevel] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [blob, setBlob] = useState<Blob | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const rafRef = useRef<number | null>(null);
  const startedAtRef = useRef(0);
  // Seconds already banked by earlier record→pause stretches; the clock is this
  // plus the time since the current stretch started, so a pause does not count.
  const bankedRef = useRef(0);

  const teardown = useCallback(() => {
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    void audioCtxRef.current?.close();
    audioCtxRef.current = null;
    setLevel(0);
  }, []);

  useEffect(() => teardown, [teardown]);
  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  const start = async () => {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
      });
      streamRef.current = stream;

      // Live level meter, so it is obvious the mic is actually picking something up.
      const ctx = new AudioContext();
      audioCtxRef.current = ctx;
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      ctx.createMediaStreamSource(stream).connect(analyser);
      const buffer = new Uint8Array(analyser.frequencyBinCount);
      const tick = () => {
        rafRef.current = requestAnimationFrame(tick);
        if (recorderRef.current?.state !== "recording") return;
        analyser.getByteTimeDomainData(buffer);
        let peak = 0;
        for (const sample of buffer) peak = Math.max(peak, Math.abs(sample - 128));
        setLevel(Math.min(1, (peak / 128) * 2.2));
        setElapsed(bankedRef.current + (Date.now() - startedAtRef.current) / 1000);
      };

      const mimeType = pickMimeType();
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      chunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onstop = () => {
        const recorded = new Blob(chunksRef.current, {
          type: recorder.mimeType || "audio/webm",
        });
        setBlob(recorded);
        setPreviewUrl(URL.createObjectURL(recorded));
        setPhase("review");
        teardown();
      };
      recorderRef.current = recorder;
      startedAtRef.current = Date.now();
      bankedRef.current = 0;
      setElapsed(0);
      recorder.start(1000);
      setPhase("recording");
      rafRef.current = requestAnimationFrame(tick);
    } catch (err) {
      teardown();
      setError(
        err instanceof Error
          ? `Could not start recording: ${err.message}. Check the microphone permission for this site.`
          : "Could not start recording.",
      );
      setPhase("idle");
    }
  };

  // MediaRecorder.pause() drops the gap from the file rather than writing silence
  // into it, so the pipeline's pause and hesitation metrics never see the break.
  const pause = () => {
    const recorder = recorderRef.current;
    if (recorder?.state !== "recording") return;
    recorder.pause();
    bankedRef.current += (Date.now() - startedAtRef.current) / 1000;
    setElapsed(bankedRef.current);
    setLevel(0);
    setPhase("paused");
  };

  const resume = () => {
    const recorder = recorderRef.current;
    if (recorder?.state !== "paused") return;
    startedAtRef.current = Date.now();
    recorder.resume();
    setPhase("recording");
  };

  const stop = () => recorderRef.current?.stop();

  const discard = () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setBlob(null);
    setPreviewUrl(null);
    bankedRef.current = 0;
    setElapsed(0);
    setPhase("idle");
  };

  const upload = async () => {
    if (!blob) return;
    setPhase("uploading");
    setError(null);
    try {
      const extension = blob.type.includes("mp4")
        ? "m4a"
        : blob.type.includes("ogg")
          ? "ogg"
          : "webm";
      await api.uploadRecording(sessionId, blob, `${sessionId}.${extension}`);
      discard();
      onUploaded();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed.");
      setPhase("review");
    }
  };

  return (
    <div>
      {error && <p className="error">{error}</p>}

      {phase === "idle" && (
        <div className="recorder">
          <button className="record" onClick={start}>
            ● Record
          </button>
          <span className="secondary small">
            Speak for 2–4 minutes. Aim for structure: context, problem, what you did,
            what happened.
          </span>
        </div>
      )}

      {(phase === "recording" || phase === "paused") && (
        <div className="recorder">
          <button className="record recording" onClick={stop}>
            ■ Stop
          </button>
          {phase === "recording" ? (
            <button onClick={pause}>❚❚ Pause</button>
          ) : (
            <button onClick={resume}>● Resume</button>
          )}
          <span
            className={phase === "paused" ? "rec-dot paused" : "rec-dot"}
            aria-hidden="true"
          />
          <span className="rec-time">{formatClock(elapsed)}</span>
          {phase === "paused" ? (
            <span className="secondary small">Paused — the gap is not recorded.</span>
          ) : (
            <span className="level" role="meter" aria-label="Input level">
              <span style={{ width: `${Math.round(level * 100)}%` }} />
            </span>
          )}
        </div>
      )}

      {(phase === "review" || phase === "uploading") && previewUrl && (
        <div>
          <audio src={previewUrl} controls style={{ width: "100%" }} />
          <div className="btn-row" style={{ marginTop: "0.75rem" }}>
            <button className="primary" onClick={upload} disabled={phase === "uploading"}>
              {phase === "uploading" ? "Uploading…" : "Save recording"}
            </button>
            <button onClick={discard} disabled={phase === "uploading"}>
              Record again
            </button>
            <span className="secondary small">{formatClock(elapsed)} captured</span>
          </div>
        </div>
      )}
    </div>
  );
}
