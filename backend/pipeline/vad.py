"""Silero VAD - a speech/silence map computed independently of the transcript.

This is the half of the design that makes hesitation detection reliable (PRD 5.2):
Whisper silently drops "um"/"uh", but a vocalized filler still shows up here as a
speech segment, so comparing this map against the aligned words exposes it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from itertools import pairwise

import numpy as np

from .audio import SAMPLE_RATE

log = logging.getLogger(__name__)


@dataclass
class Span:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 3)


@lru_cache
def _model():
    from silero_vad import load_silero_vad

    return load_silero_vad()


def speech_spans(
    audio: np.ndarray,
    *,
    sample_rate: int = SAMPLE_RATE,
    min_silence_ms: int = 250,
    min_speech_ms: int = 120,
    speech_pad_ms: int = 30,
) -> list[Span]:
    """Voiced spans in seconds. Empty list means we could not run the VAD."""
    import torch
    from silero_vad import get_speech_timestamps

    try:
        stamps = get_speech_timestamps(
            torch.from_numpy(np.ascontiguousarray(audio)),
            _model(),
            sampling_rate=sample_rate,
            return_seconds=True,
            min_silence_duration_ms=min_silence_ms,
            min_speech_duration_ms=min_speech_ms,
            speech_pad_ms=speech_pad_ms,
        )
    except Exception as exc:  # pragma: no cover - model/runtime dependent
        log.warning("Silero VAD failed, continuing without the acoustic layer: %s", exc)
        return []
    return [Span(round(float(s["start"]), 3), round(float(s["end"]), 3)) for s in stamps]


def silence_spans(spans: list[Span]) -> list[Span]:
    """Gaps between voiced spans. Leading/trailing silence is excluded: it is dead
    air around the answer, not hesitation inside it."""
    if not spans:
        return []
    return [Span(prev.end, nxt.start) for prev, nxt in pairwise(spans) if nxt.start > prev.end]


def voiced_seconds(spans: list[Span]) -> float:
    return round(sum(s.duration for s in spans), 3)
