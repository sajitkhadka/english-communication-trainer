"""The Claude-facing view of a recording.

`transcripts/<id>.json` is the complete artefact - it carries per-word timings for
subtitle sync, which is most of its size and none of its linguistic value. The brief
renders the same data as annotated, numbered sentences: every measurement the backend
took is attached to the sentence it happened in, so the skill can cite "S7" instead of
a timestamp, and a 5-minute answer costs a few hundred tokens instead of several
thousand. See docs/commands.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import db as dbmod
from .paths import abspath, transcript_path


def load_transcript(session_id: int, path: str | Path | None = None) -> dict[str, Any]:
    file = Path(path) if path else transcript_path(session_id)
    if not file.is_file():
        raise FileNotFoundError(
            f"no transcript for session {session_id} at {file} - "
            f"run `ect session transcribe {session_id}` first"
        )
    return json.loads(file.read_text(encoding="utf-8"))


def _fmt_duration(seconds: float | None) -> str:
    if not seconds:
        return "?"
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes}m {secs:02d}s" if minutes else f"{secs}s"


def _num(value: Any, suffix: str = "") -> str:
    return "?" if value is None else f"{value}{suffix}"


def build_markdown(
    transcript: dict[str, Any],
    *,
    word_meta: dict[str, dict[str, Any]] | None = None,
    recent_scores: list[dict[str, Any]] | None = None,
    max_sentences: int | None = None,
) -> str:
    word_meta = word_meta or {}
    lines: list[str] = []
    sid = transcript.get("session_id")
    mode = transcript.get("mode", "?")
    category = transcript.get("category")

    header = f"# Session {sid} - {mode}"
    if category:
        header += f" (category: {category})"
    lines += [header, ""]
    if transcript.get("topic"):
        lines.append(f"**Topic / question:** {transcript['topic']}")

    targets = transcript.get("target_words") or []
    if targets:
        described = []
        for term in targets:
            meta = word_meta.get(term.lower(), {})
            meaning = meta.get("meaning")
            kind = meta.get("kind")
            label = term
            if kind or meaning:
                label += f" ({kind or 'word'}{': ' + meaning if meaning else ''})"
            described.append(label)
        lines.append("**Target vocabulary:** " + "; ".join(described))
    elif mode == "recommended":
        lines.append("**Target vocabulary:** none recorded for this session")
    lines.append("")

    # --- measurements -------------------------------------------------------
    speech = transcript.get("speech", {})
    audio = transcript.get("audio", {})
    pauses = transcript.get("pauses", {})
    fillers = transcript.get("fillers", {})
    textual = fillers.get("textual", {})
    acoustic = fillers.get("acoustic", {})

    lines += ["## Backend measurements", ""]
    lines.append(
        f"- **Pace:** {_fmt_duration(audio.get('duration_sec'))} of audio, "
        f"{_num(speech.get('words_total'))} words, "
        f"{_num(speech.get('wpm_overall'))} wpm overall / "
        f"{_num(speech.get('wpm_speaking'))} wpm while actually speaking, "
        f"speech ratio {_num(speech.get('speech_ratio'))}"
    )
    lines.append(
        f"- **Sentences:** {_num(speech.get('sentence_count'))}, "
        f"avg {_num(speech.get('avg_sentence_words'))} words each"
    )
    buckets = ", ".join(f"{k} {v}" for k, v in (pauses.get("buckets") or {}).items())
    lines.append(
        f"- **Pauses:** {_num(pauses.get('count'))} "
        f"({_num(pauses.get('per_minute'))}/min), longest "
        f"{_num(pauses.get('longest_sec'), 's')}, "
        f"{_num(pauses.get('mid_sentence_count'))} mid-sentence"
        + (f" [{buckets}]" if buckets else "")
    )
    by_term = textual.get("by_term") or {}
    term_summary = ", ".join(f"{t} x{c}" for t, c in by_term.items()) or "none"
    lines.append(
        f"- **Textual fillers:** {_num(textual.get('total'))} "
        f"({_num(textual.get('per_minute'))}/min) - {term_summary}. "
        f"{_num(textual.get('hard_total'))} unambiguous, "
        f"{_num(textual.get('ambiguous_total'))} context-dependent "
        f"(judge those yourself - `like`, `so`, `actually`, `basically` can be legitimate)."
    )
    lines.append(
        f"- **Acoustic hesitations:** {_num(acoustic.get('total'))} "
        f"({_num(acoustic.get('per_minute'))}/min) - voiced spans with no aligned words, "
        f"i.e. fillers Whisper dropped. Treat these as real even though they are absent "
        f"from the text."
    )
    lines.append(
        f"- **Combined filler + hesitation rate:** "
        f"{_num(fillers.get('combined_per_minute'))}/min "
        f"({_num(fillers.get('combined_total'))} total)"
    )
    lines.append("")

    # --- target words -------------------------------------------------------
    hits = transcript.get("target_word_hits") or []
    if hits:
        lines += ["## Target-word usage (backend match; correctness is your call)", ""]
        for hit in hits:
            if hit.get("found"):
                where = ", ".join(
                    f"S{o['sentence'] + 1}" if o.get("sentence") is not None else "S?"
                    for o in hit.get("occurrences", [])
                )
                said = {o.get("said_as") for o in hit.get("occurrences", [])}
                lines.append(
                    f"- `{hit['term']}` - USED {hit.get('count')}x in {where} "
                    f"(said as: {', '.join(sorted(s for s in said if s))})"
                )
            else:
                lines.append(f"- `{hit['term']}` - **NOT USED**")
        lines.append("")

    # --- annotated transcript ----------------------------------------------
    lines += [
        "## Transcript",
        "",
        "Numbered sentences. `!` lines are backend measurements for that sentence, "
        "not transcript text.",
        "",
    ]
    sentences = transcript.get("transcript", {}).get("sentences") or []
    annotations = _annotations_by_sentence(transcript, len(sentences))

    shown = sentences if max_sentences is None else sentences[:max_sentences]
    for sentence in shown:
        idx = sentence.get("i", 0)
        pace = f"[{sentence['wpm']} wpm] " if sentence.get("wpm") else ""
        lines.append(f"S{idx + 1}. {pace}{sentence.get('text', '').strip()}")
        notes = annotations.get(idx)
        if notes:
            lines.append(f"    ! {' | '.join(notes)}")
    if max_sentences is not None and len(sentences) > max_sentences:
        lines.append(f"... ({len(sentences) - max_sentences} more sentences omitted)")
    if not sentences:
        lines.append("_(no sentences segmented - raw text below)_")
        lines.append("")
        lines.append(transcript.get("transcript", {}).get("text", ""))
    lines.append("")

    # --- history ------------------------------------------------------------
    if recent_scores:
        lines += ["## Recent overall scores (for continuity, not for grading on a curve)", ""]
        trail = " -> ".join(
            f"S{s['session_id']} {s['overall']}" for s in recent_scores if s.get("overall") is not None
        )
        lines.append(trail or "_none yet_")
        lines.append("")

    meta = transcript.get("meta", {})
    lines.append(
        f"_Pipeline: {meta.get('model')} @ {meta.get('compute_type')}, "
        f"aligned={meta.get('aligned')}, vad={meta.get('vad')}, "
        f"generated {meta.get('generated_at')}_"
    )
    return "\n".join(lines)


def _annotations_by_sentence(transcript: dict[str, Any], count: int) -> dict[int, list[str]]:
    """Attach fillers, pauses and hesitations to the sentence they occurred in."""
    notes: dict[int, list[str]] = {}
    sentences = transcript.get("transcript", {}).get("sentences") or []

    def bucket_for_time(start: float | None) -> int | None:
        if start is None:
            return None
        chosen = None
        for s in sentences:
            if s.get("start") is None:
                continue
            if s.get("end") is not None and s["start"] <= start <= s["end"]:
                return s.get("i")
            if s["start"] <= start:
                chosen = s.get("i")
        return chosen

    filler_terms: dict[int, list[str]] = {}
    for item in transcript.get("fillers", {}).get("textual", {}).get("items", []):
        idx = bucket_for_time(item.get("start"))
        if idx is None:
            continue
        label = item["term"] + ("?" if item.get("ambiguous") else "")
        filler_terms.setdefault(idx, []).append(label)
    for idx, terms in filler_terms.items():
        notes.setdefault(idx, []).append("fillers: " + ", ".join(terms))

    for item in transcript.get("pauses", {}).get("items", []):
        idx = item.get("sentence")
        if idx is None:
            idx = bucket_for_time(item.get("start"))
        if idx is None:
            continue
        where = f' after "{item["after_word"]}"' if item.get("after_word") else ""
        mid = ", mid-sentence" if item.get("mid_sentence") else ""
        notes.setdefault(idx, []).append(f"pause {item['dur']}s{where}{mid}")

    for item in transcript.get("fillers", {}).get("acoustic", {}).get("items", []):
        idx = item.get("sentence")
        if idx is None:
            idx = bucket_for_time(item.get("start"))
        if idx is None:
            continue
        where = f' after "{item["after_word"]}"' if item.get("after_word") else ""
        notes.setdefault(idx, []).append(f"hesitation {item['dur']}s{where} (untranscribed)")

    return {
        k: _dedupe(v)
        for k, v in notes.items()
        if k is not None and 0 <= k < max(count, 1)
    }


def _dedupe(items: list[str]) -> list[str]:
    """Two gaps either side of the same word render identically; show one."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def brief_for_session(
    session_id: int, *, max_sentences: int | None = None, history: int = 5
) -> str:
    """Full brief for one session, enriching target words from the vocabulary DB."""
    with dbmod.cursor() as conn:
        session = dbmod.get_session(conn, session_id)
        if session is None:
            raise LookupError(f"session {session_id} does not exist")
        path = abspath(session.get("transcript_path"))
        transcript = load_transcript(session_id, path)
        word_meta = {}
        for term in transcript.get("target_words") or []:
            row = dbmod.get_word_by_term(conn, term)
            if row:
                word_meta[term.lower()] = row
        recent = [
            s
            for s in dbmod.score_history(conn, limit=200)
            if s["session_id"] != session_id
        ][-history:]
    return build_markdown(
        transcript,
        word_meta=word_meta,
        recent_scores=recent,
        max_sentences=max_sentences,
    )
