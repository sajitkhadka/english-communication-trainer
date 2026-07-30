import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { Transcript } from "../types";
import { formatClock } from "./common";

/** Audio player with subtitles synced to the aligned word timings, plus a pause map.
 *  Word timings come from wav2vec2 forced alignment, so highlighting tracks the
 *  waveform rather than Whisper's interpolated segment times. */
export default function Playback({ sessionId, transcript }: { sessionId: number; transcript: Transcript }) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const rafRef = useRef<number | null>(null);
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);

  const duration = transcript.audio.duration_sec || 1;
  const sentences = transcript.transcript.sentences;
  const words = transcript.transcript.words;

  // timeupdate fires ~4x/second, too coarse to track a word. Poll on a frame instead,
  // but only while playing so an idle tab does no work.
  const follow = useCallback(() => {
    const audio = audioRef.current;
    if (!audio) return;
    setTime(audio.currentTime);
    rafRef.current = requestAnimationFrame(follow);
  }, []);

  useEffect(() => {
    if (playing) rafRef.current = requestAnimationFrame(follow);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    };
  }, [playing, follow]);

  const seek = (seconds: number) => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.currentTime = Math.max(0, Math.min(duration, seconds));
    setTime(audio.currentTime);
  };

  const activeSentence = useMemo(() => {
    let found: (typeof sentences)[number] | null = null;
    for (const sentence of sentences) {
      if (sentence.start == null) continue;
      if (sentence.start <= time && (sentence.end == null || time <= sentence.end)) return sentence;
      if (sentence.start <= time) found = sentence;
    }
    return found;
  }, [sentences, time]);

  const activeWords = useMemo(() => {
    if (!activeSentence) return [];
    const from = activeSentence.start ?? 0;
    const to = activeSentence.end ?? from;
    return words.filter((word) => word.s != null && word.s >= from - 0.01 && word.s <= to + 0.01);
  }, [activeSentence, words]);

  // Annotations, so the transcript list shows what the analysis measured where.
  const annotations = useMemo(() => {
    const map = new Map<number, string[]>();
    const push = (index: number | null, note: string) => {
      if (index == null) return;
      const list = map.get(index) ?? [];
      if (!list.includes(note)) list.push(note);
      map.set(index, list);
    };
    for (const item of transcript.fillers.textual.items) {
      const index = sentenceAt(sentences, item.start);
      push(index, item.ambiguous ? `${item.term}?` : item.term);
    }
    for (const item of transcript.pauses.items) {
      push(item.sentence ?? sentenceAt(sentences, item.start), `pause ${item.dur}s`);
    }
    for (const item of transcript.fillers.acoustic.items) {
      push(item.sentence ?? sentenceAt(sentences, item.start), `hesitation ${item.dur}s`);
    }
    return map;
  }, [transcript, sentences]);

  return (
    <div>
      <audio
        ref={audioRef}
        src={`/api/sessions/${sessionId}/audio`}
        controls
        preload="metadata"
        style={{ width: "100%" }}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onSeeked={(event) => setTime(event.currentTarget.currentTime)}
      />

      <div className="subtitle" style={{ marginTop: "0.85rem" }} aria-live="off">
        {activeWords.length > 0 ? (
          activeWords.map((word, index) => {
            const state = word.s == null ? "ahead" : time >= word.s ? (isNow(word, time) ? "now" : "spoken") : "ahead";
            return (
              <span key={`${index}-${word.w}`} className={`w ${state}`}>
                {word.w}{" "}
              </span>
            );
          })
        ) : (
          <span className="muted">Press play to follow the transcript.</span>
        )}
      </div>

      <PauseMap transcript={transcript} time={time} onSeek={seek} />

      <div className="card-head" style={{ marginTop: "1.25rem" }}>
        <h3>Transcript</h3>
        <span className="muted small">Click a line to jump to it</span>
      </div>
      <div className="transcript-lines">
        {sentences.map((sentence) => {
          const notes = annotations.get(sentence.i);
          return (
            <button
              key={sentence.i}
              className={`tline ${activeSentence?.i === sentence.i ? "active" : ""}`}
              onClick={() => seek(sentence.start ?? 0)}
            >
              <span className="ts">{formatClock(sentence.start ?? 0)}</span>
              <span>
                {sentence.text}
                {notes && notes.length > 0 && <span className="anno">{notes.join(" · ")}</span>}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function isNow(word: { s: number | null; e: number | null }, time: number): boolean {
  if (word.s == null) return false;
  const end = word.e ?? word.s + 0.3;
  return time >= word.s && time <= end;
}

function sentenceAt(
  sentences: Transcript["transcript"]["sentences"],
  start: number | null,
): number | null {
  if (start == null) return null;
  let found: number | null = null;
  for (const sentence of sentences) {
    if (sentence.start == null) continue;
    if (sentence.end != null && sentence.start <= start && start <= sentence.end) return sentence.i;
    if (sentence.start <= start) found = sentence.i;
  }
  return found;
}

/** Timeline of where the silence and the untranscribed hesitations actually fall.
 *  Three categories, each labeled in the legend — identity is never color-alone. */
export function PauseMap({
  transcript,
  time,
  onSeek,
}: {
  transcript: Transcript;
  time: number;
  onSeek: (seconds: number) => void;
}) {
  const duration = transcript.audio.duration_sec || 1;
  const pct = (value: number) => `${Math.max(0, Math.min(100, (value / duration) * 100))}%`;

  const handleClick = (event: React.MouseEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    onSeek(((event.clientX - rect.left) / rect.width) * duration);
  };

  return (
    <div style={{ marginTop: "1.25rem" }}>
      <div className="legend">
        <span className="key">
          <span className="swatch" style={{ background: "var(--series-1)", opacity: 0.55 }} />
          Speech
        </span>
        <span className="key">
          <span className="swatch" style={{ background: "var(--axis)" }} />
          Pause ({transcript.pauses.count})
        </span>
        <span className="key">
          <span className="swatch" style={{ background: "var(--series-2)" }} />
          Untranscribed hesitation ({transcript.fillers.acoustic.total})
        </span>
      </div>

      <div className="pausemap" onClick={handleClick} role="presentation">
        <div className="seg speech" style={{ left: 0, width: "100%" }} />
        {transcript.pauses.items.map((pause, index) => (
          <div
            key={`p${index}`}
            className="seg pause"
            style={{ left: pct(pause.start), width: pct(pause.dur) }}
            title={`${pause.dur}s pause${pause.after_word ? ` after "${pause.after_word}"` : ""}`}
          />
        ))}
        {transcript.fillers.acoustic.items.map((hesitation, index) => (
          <div
            key={`h${index}`}
            className="seg hesitation"
            style={{ left: pct(hesitation.start), width: `max(3px, ${pct(hesitation.dur)})` }}
            title={`${hesitation.dur}s vocalized hesitation${
              hesitation.after_word ? ` after "${hesitation.after_word}"` : ""
            }`}
          />
        ))}
        <div className="cursor" style={{ left: pct(time) }} />
      </div>
      <div className="axis-row">
        <span>0:00</span>
        <span>{formatClock(duration)}</span>
      </div>
    </div>
  );
}
