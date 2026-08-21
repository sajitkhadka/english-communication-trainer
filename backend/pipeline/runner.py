"""Assembles `data/transcripts/<id>.json` - the single artefact Claude reads (PRD 5.4).

Everything expensive and deterministic happens here so the skill only ever loads
pre-chewed structure.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from app.config import settings
from app.paths import transcript_path, transcript_text_path

from .audio import duration_of, load_audio
from .metrics import analyse, apply_cross_check, find_hidden_fillers, per_minute, wpm
from .parakeet import transcribe_timed as parakeet_transcribe
from .transcribe import transcribe
from .vad import speech_spans

log = logging.getLogger(__name__)

PIPELINE_VERSION = 1


def run(
    session_id: int,
    audio_path: Path | str,
    *,
    mode: str = "freeform",
    topic: str | None = None,
    category: str | None = None,
    target_words: list[str] | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """Transcribe + analyse one recording. Returns the transcript.json payload."""
    started = time.perf_counter()
    audio_path = Path(audio_path)

    audio = load_audio(audio_path)
    duration = duration_of(audio_path) or round(len(audio) / 16_000, 3)

    result = transcribe(audio, target_words=target_words)
    words = result.words
    spans = speech_spans(audio)

    analysis = analyse(
        words,
        spans,
        duration,
        fallback_segments=result.segments,
        target_words=target_words or [],
    )

    # A second, independent ASR pass: catches target words Whisper's language-model
    # prior corrected away, and disfluencies it smoothed into clean prose. Never
    # allowed to fail the whole transcription - see pipeline/parakeet.py.
    parakeet_pass = False
    cross_check_engine: str | None = None
    try:
        parakeet_words = parakeet_transcribe(audio, spans=spans)
        if parakeet_words:
            parakeet_pass = True
            if target_words:
                heard = [w.word for w in parakeet_words]
                analysis.target_hits = apply_cross_check(analysis.target_hits, heard)
                cross_check_engine = "parakeet"
            analysis.hesitations, analysis.hidden_fillers = find_hidden_fillers(
                parakeet_words, analysis.textual_fillers, analysis.hesitations, analysis.sentences
            )
    except Exception:
        log.warning("Parakeet cross-check pass failed, continuing without it", exc_info=True)

    payload = build_payload(
        session_id=session_id,
        mode=mode,
        topic=topic,
        category=category,
        target_words=target_words or [],
        result=result,
        analysis=analysis,
        audio_path=audio_path,
        elapsed=round(time.perf_counter() - started, 2),
        cross_check_engine=cross_check_engine,
        parakeet_pass=parakeet_pass,
    )

    if write:
        out = transcript_path(session_id)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        # Plain-text sibling: no word timings, no measurements - what content-only
        # briefs and journal entries read instead of parsing the full JSON (app/brief.py).
        transcript_text_path(session_id).write_text(payload["transcript"]["text"], encoding="utf-8")
        log.info("wrote %s (%.1fs audio in %.1fs)", out, duration, payload["meta"]["elapsed_sec"])
    return payload


def build_payload(
    *,
    session_id: int,
    mode: str,
    topic: str | None,
    category: str | None,
    target_words: list[str],
    result: Any,
    analysis: Any,
    audio_path: Path,
    elapsed: float,
    cross_check_engine: str | None = None,
    parakeet_pass: bool = False,
) -> dict[str, Any]:
    duration = analysis.duration_sec
    speaking = analysis.speaking_sec or duration

    hard_fillers = [h for h in analysis.textual_fillers if not h.ambiguous]
    soft_fillers = [h for h in analysis.textual_fillers if h.ambiguous]
    filler_total = len(hard_fillers) + len(analysis.hesitations)

    by_term: dict[str, int] = {}
    for hit in analysis.textual_fillers:
        by_term[hit.term] = by_term.get(hit.term, 0) + 1

    return {
        "session_id": session_id,
        "mode": mode,
        "category": category,
        "topic": topic,
        "target_words": target_words,
        "audio": {
            "file": audio_path.name,
            "duration_sec": duration,
            "sample_rate": 16_000,
        },
        "transcript": {
            "text": result.text,
            "sentences": [
                {
                    "i": s.index,
                    "text": s.text,
                    "start": s.start,
                    "end": s.end,
                    "words": s.word_count,
                    "wpm": wpm(s.word_count, s.duration),
                }
                for s in analysis.sentences
            ],
            # Word-level timings power subtitle sync in the frontend. The skill reads
            # the slim brief instead (see pipeline/brief.py), so this costs no tokens.
            "words": [{"w": w.word, "s": w.start, "e": w.end} for w in result.words],
        },
        "speech": {
            "words_total": analysis.word_count,
            "wpm_overall": wpm(analysis.word_count, duration),
            "wpm_speaking": wpm(analysis.word_count, speaking),
            "speaking_sec": analysis.speaking_sec,
            "silence_sec": analysis.silence_sec,
            "speech_ratio": round(speaking / duration, 3) if duration else None,
            "sentence_count": len(analysis.sentences),
            "avg_sentence_words": round(analysis.word_count / len(analysis.sentences), 1)
            if analysis.sentences
            else None,
        },
        "pauses": {
            "count": len(analysis.pauses),
            "per_minute": per_minute(len(analysis.pauses), duration),
            "total_sec": round(sum(p.duration for p in analysis.pauses), 3),
            "longest_sec": max((p.duration for p in analysis.pauses), default=0.0),
            "mid_sentence_count": sum(1 for p in analysis.pauses if p.mid_sentence),
            "buckets": _buckets(analysis.pauses),
            "items": [
                {
                    "start": p.start,
                    "dur": p.duration,
                    "after_word": p.after_word,
                    "sentence": p.sentence_index,
                    "mid_sentence": p.mid_sentence,
                }
                for p in analysis.pauses
            ],
        },
        "fillers": {
            "textual": {
                "total": len(analysis.textual_fillers),
                "hard_total": len(hard_fillers),
                "ambiguous_total": len(soft_fillers),
                "per_minute": per_minute(len(analysis.textual_fillers), duration),
                "by_term": dict(sorted(by_term.items(), key=lambda kv: -kv[1])),
                "items": [
                    {
                        "term": h.term,
                        "start": h.start,
                        "ambiguous": h.ambiguous,
                        "word_index": h.word_index,
                    }
                    for h in analysis.textual_fillers
                ],
            },
            "acoustic": {
                "total": len(analysis.hesitations),
                "per_minute": per_minute(len(analysis.hesitations), duration),
                "note": (
                    "Voiced spans with (almost) no aligned words - vocalized fillers "
                    "Whisper normalised out of the transcript. `heard_as`, when present, "
                    "is what a second ASR pass identified the sound as."
                ),
                "items": [
                    {
                        "start": h.start,
                        "dur": h.duration,
                        "word_coverage": h.word_coverage,
                        "after_word": h.after_word,
                        "sentence": h.sentence_index,
                        "heard_as": h.heard_as,
                    }
                    for h in analysis.hesitations
                ],
            },
            "combined_total": filler_total,
            "combined_per_minute": per_minute(filler_total, duration),
            "cross_check": {
                "total": len(analysis.hidden_fillers),
                "per_minute": per_minute(len(analysis.hidden_fillers), duration),
                "note": (
                    "Filler/hesitation sounds a second ASR pass (Parakeet) heard that "
                    "Whisper's transcript shows no trace of at all - not in the text, and "
                    "not as an acoustic hesitation above either. Not included in "
                    "combined_total/combined_per_minute; use your judgement on whether "
                    "these push the fluency score down further."
                ),
                "items": [
                    {"term": h.term, "start": h.start, "sentence": h.sentence_index}
                    for h in analysis.hidden_fillers
                ],
            },
        },
        "target_word_hits": [
            {
                "term": t.term,
                "found": t.found,
                "count": t.count,
                "occurrences": t.occurrences,
                "confirmed_by": t.confirmed_by,
            }
            for t in analysis.target_hits
        ],
        "meta": {
            "pipeline_version": PIPELINE_VERSION,
            "model": result.model,
            "compute_type": result.compute_type,
            "aligned": result.aligned,
            "language": result.language,
            "vad": "silero",
            "parakeet_pass": parakeet_pass,
            "target_word_cross_check": cross_check_engine,
            "elapsed_sec": elapsed,
            "generated_at": _now(),
            "thresholds": {
                "min_pause_sec": settings.min_pause_sec,
                "long_pause_sec": settings.long_pause_sec,
                "min_hesitation_sec": settings.min_hesitation_sec,
                "hesitation_max_word_coverage": settings.hesitation_max_word_coverage,
            },
        },
    }


def _buckets(pauses: list[Any]) -> dict[str, int]:
    from .metrics import pause_buckets

    raw = pause_buckets(pauses)
    return {
        f"short_{settings.min_pause_sec}_0.7": raw["short"],
        f"medium_0.7_{settings.long_pause_sec}": raw["medium"],
        f"long_{settings.long_pause_sec}_plus": raw["long"],
    }


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds")
