# ADR 0002 — WhisperX for words, Silero VAD for silence

**Status:** Accepted · **Date:** 2026-07-30 · **PRD:** §5

## Context

Filler and hesitation analysis is a primary goal, and it runs into a known property of
the whole Whisper family: **the models normalise speech and silently drop disfluencies.**
"So, um, the thing is…" often transcribes as "So the thing is…". Any analysis that reads
only the transcript will under-report hesitation, and will do so invisibly — the user
gets told their delivery is clean when it is not.

Transcription must also stay local on a 6 GB laptop GPU (no cloud APIs), and word-level
timestamps need to be accurate enough to drive subtitle sync.

## Decision

Run **two independent layers** over every recording and compare them:

1. **WhisperX** (`large-v3`, `int8_float16`) for the transcript, plus wav2vec2 forced
   alignment for real word-level timestamps. Raw Whisper timestamps are interpolated
   from decoder attention and drift; forced alignment measures against the waveform.
2. **Silero VAD** for a speech/silence map computed from the audio alone, with no
   knowledge of the transcript.

The signal that makes this work: **a voiced span containing (almost) no aligned words is
a vocalization the transcript lost.** `pipeline/metrics.py:find_hesitations` reports any
VAD speech span longer than 0.25 s whose overlap with aligned words is under 40 % as an
acoustic hesitation. Textual fillers that *were* captured are counted separately, so both
layers appear in the brief and neither depends on the other.

## Alternatives

**Transcript-only filler counting.** Free, and wrong in exactly the way the user asked us
to avoid. Rejected on the grounds that its failure mode is silent.

**Raw faster-whisper timestamps, no alignment.** Saves ~360 MB and a few seconds per
clip, but timestamps drift by hundreds of milliseconds, which breaks both subtitle sync
and the coverage comparison the hesitation detector depends on. The whole design rests on
word boundaries being trustworthy.

**`word_timestamps=True` with `condition_on_previous_text` tuning, or prompting Whisper
to keep disfluencies.** Moves the drop rate but does not remove it, and how much it moves
varies per recording. Not a foundation for a metric.

**WhisperX's own bundled VAD (pyannote) as the silence map.** It already runs, for
chunking. But it is tuned for segmentation for ASR, and reusing the ASR pipeline's own
VAD to audit that pipeline's output couples the two layers. Silero is 2 MB, ships inside
its wheel, and stays genuinely independent.

## Consequences

- Hesitation reporting is robust regardless of how aggressively Whisper cleans up. In
  end-to-end testing, a clip whose "Umm" was normalised out of the text still surfaced two
  acoustic hesitations.
- Two models must coexist in 6 GB. `large-v3 @ int8_float16` (~3 GB) + wav2vec2 (~0.3 GB)
  + Silero (~2 MB) fits, and `transcribe.py` walks a documented precision ladder if it
  does not (see `docs/setup.md`).
- The acoustic layer is threshold-driven, so it is tunable and it is fallible: a long
  breath can read as a hesitation. Both thresholds are env-overridable, and the brief
  labels these items `(untranscribed)` so the model weighs them as evidence rather than
  fact.
- Alignment failure degrades rather than breaks: `meta.aligned: false` is recorded, and
  pause analysis continues from Silero alone.
