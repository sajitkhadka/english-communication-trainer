"""Parakeet-TDT via onnx-asr - a second, independent ASR pass used to cross-check
target-word detection and to catch disfluencies Whisper's transcript erases
entirely (see docs/adr for the rationale).

Whisper's transcript stays what Claude reads; this model never produces a
transcript anyone sees. It exists because Whisper's language-model prior both
"corrects" unusual target words into something more common and smooths real
self-corrections ("and it's uh, this is...") into clean prose - a transducer
model with a different training lineage tends to preserve both instead.

Deliberately not cached like `transcribe.load_asr`: loaded once alongside
Whisper + the wav2vec2 aligner, this model leaves 0 bytes of free VRAM on a
6 GB card (measured), which is fine until anything else touches the GPU and
then is not. It is loaded fresh for this pass and released immediately after -
a few seconds of extra latency in exchange for never holding two ASR models in
VRAM at once.
"""

from __future__ import annotations

import gc
import logging

import numpy as np

from .audio import SAMPLE_RATE
from .gpu import prepare_cuda_dlls
from .transcribe import Word
from .vad import Span, speech_spans

log = logging.getLogger(__name__)

_MODEL_NAME = "nemo-parakeet-tdt-0.6b-v3"
# Feeding onnx-asr audio longer than ~30s in one call silently drops whole
# sentences (observed on a real 4-minute recording); staying well under that
# and always cutting on a silence boundary avoids it.
_MAX_CHUNK_SEC = 20.0


def _merge_chunks(spans: list[Span], max_chunk_sec: float = _MAX_CHUNK_SEC) -> list[Span]:
    """Merge fine-grained VAD spans into windows capped at `max_chunk_sec`,
    always cutting between spans rather than mid-speech."""
    if not spans:
        return []
    chunks: list[Span] = []
    cur_start, cur_end = spans[0].start, spans[0].end
    for s in spans[1:]:
        if s.end - cur_start > max_chunk_sec:
            chunks.append(Span(cur_start, cur_end))
            cur_start, cur_end = s.start, s.end
        else:
            cur_end = s.end
    chunks.append(Span(cur_start, cur_end))
    return chunks


def _words_from_tokens(
    tokens: list[str], timestamps: list[float], *, offset: float = 0.0
) -> list[Word]:
    """Merge onnx-asr's subword tokens into whole words with absolute start
    times. A token starting a new word is prefixed with a space (`" is"`,
    `" dem"` + `"o"` -> "demo"); punctuation and word-continuation pieces carry
    no leading space and attach to the word in progress."""
    words: list[Word] = []
    buf = ""
    start: float | None = None
    for tok, ts in zip(tokens, timestamps, strict=True):
        piece = tok.strip()
        if not piece:
            continue
        if not tok.startswith(" ") and buf:
            buf += piece
            continue
        if buf:
            words.append(Word(word=buf, start=start, end=None))
        buf, start = piece, offset + ts
    if buf:
        words.append(Word(word=buf, start=start, end=None))
    return words


def _load_model():
    prepare_cuda_dlls()
    import onnx_asr

    try:
        return onnx_asr.load_model(_MODEL_NAME)
    except Exception:
        log.warning("Parakeet GPU load failed, falling back to CPU", exc_info=True)
        return onnx_asr.load_model(_MODEL_NAME, providers=["CPUExecutionProvider"])


def transcribe_timed(audio: np.ndarray, *, spans: list[Span] | None = None) -> list[Word]:
    """Parakeet's word stream with absolute start times.

    Feeds both `metrics.apply_cross_check` (target words) and
    `metrics.find_hidden_fillers` (disfluencies) from a single pass, since
    running this model twice would double the load/inference cost for no
    reason - both only need a plain `Word` list, so there is one code path.
    """
    if spans is None:
        spans = speech_spans(audio)
    chunks = _merge_chunks(spans)
    if not chunks:
        return []

    model = _load_model().with_timestamps()
    try:
        words: list[Word] = []
        for chunk in chunks:
            seg = audio[int(chunk.start * SAMPLE_RATE) : int(chunk.end * SAMPLE_RATE)]
            if seg.size == 0:
                continue
            result = model.recognize(seg.astype(np.float32), sample_rate=SAMPLE_RATE)
            words.extend(
                _words_from_tokens(result.tokens or [], result.timestamps or [], offset=chunk.start)
            )
        return words
    finally:
        del model
        gc.collect()
