"""WhisperX transcription + wav2vec2 forced alignment (PRD 5.1).

Models are loaded once per process and cached: on 6 GB of VRAM we cannot afford
to reload `large-v3` per request, and the alignment model must coexist with it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from app.config import settings

from .gpu import prepare_cuda_dlls

log = logging.getLogger(__name__)

# PRD 5.3 fallback ladder: try the configured precision first, then degrade.
COMPUTE_FALLBACKS = {
    "int8_float16": ["int8_float16", "int8", "float16"],
    "float16": ["float16", "int8_float16", "int8"],
    "int8": ["int8"],
}

_asr_cache: dict[tuple[str, str, str], Any] = {}
_align_cache: dict[tuple[str, str], tuple[Any, Any]] = {}


@dataclass
class Word:
    word: str
    start: float | None
    end: float | None
    score: float | None = None


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


@dataclass
class TranscriptionResult:
    segments: list[Segment]
    language: str
    model: str
    compute_type: str
    aligned: bool

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments if s.text.strip()).strip()

    @property
    def words(self) -> list[Word]:
        return [w for s in self.segments for w in s.words]


def _compute_ladder(preferred: str) -> list[str]:
    return COMPUTE_FALLBACKS.get(preferred, [preferred, "int8"])


def load_asr(
    model_name: str | None = None,
    compute_type: str | None = None,
    device: str | None = None,
):
    """Load (and cache) the faster-whisper pipeline, degrading precision on OOM."""
    prepare_cuda_dlls()
    import whisperx

    model_name = model_name or settings.whisper_model
    device = device or settings.device
    preferred = compute_type or settings.compute_type

    last_error: Exception | None = None
    for candidate in _compute_ladder(preferred):
        key = (model_name, candidate, device)
        if key in _asr_cache:
            return _asr_cache[key], candidate
        try:
            settings.model_cache_dir.mkdir(parents=True, exist_ok=True)
            model = whisperx.load_model(
                model_name,
                device=device,
                compute_type=candidate,
                language=settings.language,
                download_root=str(settings.model_cache_dir),
            )
        except Exception as exc:  # OOM, unsupported compute type, driver mismatch
            log.warning("load_model(%s, %s) failed: %s", model_name, candidate, exc)
            last_error = exc
            continue
        if candidate != preferred:
            log.warning("fell back to compute_type=%s (preferred %s)", candidate, preferred)
        _asr_cache[key] = model
        return model, candidate
    raise RuntimeError(
        f"could not load Whisper model {model_name!r} at any of "
        f"{_compute_ladder(preferred)}: {last_error}"
    )


def load_aligner(language: str, device: str | None = None):
    import whisperx

    device = device or settings.device
    key = (language, device)
    if key not in _align_cache:
        settings.model_cache_dir.mkdir(parents=True, exist_ok=True)
        _align_cache[key] = whisperx.load_align_model(
            language_code=language,
            device=device,
            model_dir=str(settings.model_cache_dir),
        )
    return _align_cache[key]


def unload_models() -> None:
    """Free VRAM (used between the ASR and alignment stages if memory is tight)."""
    _asr_cache.clear()
    _align_cache.clear()
    try:
        import gc

        import torch

        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # pragma: no cover
        pass


def _to_segments(raw: list[dict[str, Any]]) -> list[Segment]:
    segments: list[Segment] = []
    for seg in raw:
        words = [
            Word(
                word=str(w.get("word", "")).strip(),
                start=_f(w.get("start")),
                end=_f(w.get("end")),
                score=_f(w.get("score")),
            )
            for w in seg.get("words", [])
            if str(w.get("word", "")).strip()
        ]
        segments.append(
            Segment(
                start=_f(seg.get("start")) or 0.0,
                end=_f(seg.get("end")) or 0.0,
                text=str(seg.get("text", "")).strip(),
                words=words,
            )
        )
    return segments


def _f(value: Any) -> float | None:
    try:
        return None if value is None else round(float(value), 3)
    except (TypeError, ValueError):
        return None


def transcribe(
    audio: np.ndarray,
    *,
    align: bool | None = None,
    target_words: list[str] | None = None,
) -> TranscriptionResult:
    """Transcribe mono 16 kHz float32 audio, then force-align for word timings.

    `target_words` are the session's practice vocabulary, known before the recording
    even starts. They're passed to Whisper as `hotwords` so its language-model prior
    is biased toward the exact word being practiced instead of "correcting" it to
    something more common-sounding - the failure mode this app can least afford,
    since target-word usage is graded from the transcript.
    """
    import whisperx

    should_align = settings.align if align is None else align
    model, compute_type = load_asr()
    # The pipeline is cached and reused across sessions (module-level dict above), so
    # its options must be set fresh per call rather than at load time. Safe to mutate:
    # `services.transcribe_session` holds a process-wide GPU lock, so only one
    # transcription touches this object at a time.
    model.options = replace(
        model.options, hotwords=", ".join(target_words) if target_words else None
    )

    result = model.transcribe(audio, batch_size=settings.batch_size)
    language = result.get("language") or settings.language
    raw_segments = result.get("segments") or []

    aligned = False
    if should_align and raw_segments:
        try:
            align_model, metadata = load_aligner(language)
            aligned_result = whisperx.align(
                raw_segments,
                align_model,
                metadata,
                audio,
                settings.device,
                return_char_alignments=False,
            )
            raw_segments = aligned_result.get("segments") or raw_segments
            aligned = True
        except Exception as exc:
            # Alignment is an accuracy upgrade, not a hard requirement: without it
            # we still have segment-level timings, and the VAD layer still gives
            # the acoustic pause map.
            log.warning("forced alignment failed, falling back to segment timings: %s", exc)

    return TranscriptionResult(
        segments=_to_segments(raw_segments),
        language=language,
        model=settings.whisper_model,
        compute_type=compute_type,
        aligned=aligned,
    )
