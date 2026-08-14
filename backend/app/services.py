"""Workflow operations shared by the HTTP API and the `ect` CLI.

The CLI path exists so the skills can work with the server stopped; the HTTP path
exists so the frontend can drive the same transitions. Both funnel through here to
keep one definition of each state change.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any

from . import db as dbmod
from . import srs
from .paths import (
    abspath,
    ensure_tree,
    feedback_path,
    find_recording,
    prompt_path,
    queue_marker,
    recording_path,
    relpath,
    transcript_path,
    worklog_daily_path,
    worklog_rollup_path,
)

log = logging.getLogger(__name__)


class WorkflowError(RuntimeError):
    """A transition the current state does not allow."""


# --------------------------------------------------------------------------- #
# creating sessions / prompts
# --------------------------------------------------------------------------- #


def create_session(
    *,
    mode: str,
    topic: str | None,
    category: str | None = None,
    target_words: list[str] | None = None,
    notes: str | None = None,
    write_prompt: bool = True,
) -> dict[str, Any]:
    if mode not in ("recommended", "freeform", "interview", "worklog"):
        raise WorkflowError(f"unknown mode {mode!r}")
    if mode in ("recommended", "interview") and not topic:
        raise WorkflowError(f"{mode} sessions need a topic")
    if mode == "worklog" and not topic:
        # Plain hyphen: the topic round-trips through Windows consoles via the CLI.
        topic = f"Worklog - {dbmod.today()}"
    ensure_tree()
    with dbmod.cursor() as conn:
        session_id = dbmod.create_session(
            conn,
            mode=mode,
            topic=topic,
            category=category,
            target_words=target_words or [],
            notes=notes,
        )
        # Register target words so they exist in the corpus before review scheduling.
        for term in target_words or []:
            dbmod.add_word(conn, term=term, source="recommended")
        session = dbmod.get_session(conn, session_id)
    if session is None:  # pragma: no cover - the row was written in this transaction
        raise WorkflowError(f"session {session_id} vanished immediately after creation")
    if write_prompt:
        write_prompt_file(session)
    return session


def write_prompt_file(session: dict[str, Any]) -> Path:
    """`data/prompts/<id>.json` - what the frontend renders before recording (PRD 7.3)."""
    payload = {
        "session_id": session["id"],
        "mode": session["mode"],
        "category": session.get("category"),
        "topic": session.get("topic"),
        "target_words": session.get("target_words") or [],
        "notes": session.get("notes"),
        "created_at": session.get("created_at"),
    }
    with dbmod.cursor() as conn:
        enriched = []
        for term in payload["target_words"]:
            row = dbmod.get_word_by_term(conn, term)
            enriched.append(
                {
                    "term": term,
                    "kind": (row or {}).get("kind"),
                    "meaning": (row or {}).get("meaning"),
                    "example": (row or {}).get("example"),
                }
            )
        payload["target_words_detail"] = enriched
    path = prompt_path(session["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# audio in
# --------------------------------------------------------------------------- #


def store_recording(session_id: int, data: bytes, *, suffix: str = ".webm") -> dict[str, Any]:
    ensure_tree()
    with dbmod.cursor() as conn:
        session = dbmod.get_session(conn, session_id)
        if session is None:
            raise WorkflowError(f"session {session_id} does not exist")
        # Replacing a recording invalidates the analysis derived from the old one.
        for stale in (transcript_path(session_id), feedback_path(session_id)):
            if stale.exists():
                stale.unlink()
        path = recording_path(session_id, session["mode"], suffix=suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

        from pipeline.audio import duration_of

        dbmod.update_session(
            conn,
            session_id,
            audio_path=relpath(path),
            status="recorded",
            transcript_path=None,
            feedback_path=None,
            transcribe_status="none",
            transcribe_error=None,
            duration_sec=duration_of(path),
        )
        session = dbmod.get_session(conn, session_id)
    if session is None:  # pragma: no cover - checked at the top of this function
        raise WorkflowError(f"session {session_id} disappeared while storing audio")
    return session


# --------------------------------------------------------------------------- #
# transcription
# --------------------------------------------------------------------------- #


# One GPU, 6 GB of VRAM, and a model cache that is only checked *before* the load:
# two concurrent runs each load their own large-v3 and the process dies natively
# (no Python exception, so `transcribe_status` would be stuck at "running" forever).
# FastAPI runs sync endpoints in a threadpool, so double-clicking Process is enough
# to trigger it. The in-process lock is the real guard - the `running` flag in the DB
# cannot be, because a crashed run leaves it set with nothing to clear it.
_gpu_lock = threading.Lock()


def transcribe_session(session_id: int, *, force: bool = False) -> dict[str, Any]:
    """Run the GPU pipeline for one session. Idempotent unless `force`.

    Refuses to start while another transcription is in flight in this process.
    """
    if not _gpu_lock.acquire(blocking=False):
        raise WorkflowError(
            "a transcription is already running - wait for it to finish, then try again"
        )
    try:
        return _transcribe_session(session_id, force=force)
    finally:
        _gpu_lock.release()


def _transcribe_session(session_id: int, *, force: bool = False) -> dict[str, Any]:
    with dbmod.cursor() as conn:
        session = dbmod.get_session(conn, session_id)
        if session is None:
            raise WorkflowError(f"session {session_id} does not exist")
        existing = transcript_path(session_id)
        if existing.is_file() and not force:
            dbmod.update_session(
                conn,
                session_id,
                transcript_path=relpath(existing),
                transcribe_status="done",
            )
            return {
                "session_id": session_id,
                "status": "already_transcribed",
                "transcript_path": relpath(existing),
            }
        audio = abspath(session.get("audio_path")) or find_recording(session_id, session["mode"])
        if audio is None or not Path(audio).is_file():
            raise WorkflowError(f"session {session_id} has no stored audio to transcribe")
        dbmod.update_session(conn, session_id, transcribe_status="running", transcribe_error=None)

    try:
        from pipeline.runner import run

        payload = run(
            session_id,
            audio,
            mode=session["mode"],
            topic=session.get("topic"),
            category=session.get("category"),
            target_words=session.get("target_words") or [],
        )
    except Exception as exc:
        log.exception("transcription failed for session %s", session_id)
        with dbmod.cursor() as conn:
            dbmod.update_session(
                conn, session_id, transcribe_status="error", transcribe_error=str(exc)[:2000]
            )
        raise

    with dbmod.cursor() as conn:
        dbmod.update_session(
            conn,
            session_id,
            transcript_path=relpath(transcript_path(session_id)),
            transcribe_status="done",
            transcribe_error=None,
            duration_sec=payload["audio"]["duration_sec"],
        )
    return {
        "session_id": session_id,
        "status": "transcribed",
        "transcript_path": relpath(transcript_path(session_id)),
        "duration_sec": payload["audio"]["duration_sec"],
        "words": payload["speech"]["words_total"],
        "elapsed_sec": payload["meta"]["elapsed_sec"],
    }


# --------------------------------------------------------------------------- #
# the queue (PRD 6.3)
# --------------------------------------------------------------------------- #


QUEUED_HINT = (
    "Flagged for Claude. Run `/process-session` in the Claude Code console to generate feedback."
)
QUEUED_HINT_WORKLOG = (
    "Flagged for Claude. Run `/log-work` in the Claude Code console to turn this "
    "recording into a journal entry."
)
NOT_QUEUED_HINT = (
    "Not queued: there is no transcript for Claude to read yet. Press Process again "
    "once the recording is available."
)


def enqueue(session_id: int, *, transcribe: bool | None = None) -> dict[str, Any]:
    """Frontend "Process" button: transcribe, then flag for the next skill run.

    Transcription is backend work with no Claude involvement, so it happens here
    rather than making the skill wait on the GPU. The session is only flipped to
    `pending` once the transcript exists: "queued for Claude" has to mean Claude
    can actually read the recording, otherwise a failed GPU run leaves a session
    advertised in the queue that `/process-session` cannot do anything with.
    """
    from .config import settings

    with dbmod.cursor() as conn:
        session = dbmod.get_session(conn, session_id)
        if session is None:
            raise WorkflowError(f"session {session_id} does not exist")
        if session["status"] == "awaiting_recording":
            raise WorkflowError(f"session {session_id} has no recording yet")
    audio = abspath(session.get("audio_path")) or find_recording(session_id, session["mode"])
    if audio is None or not Path(audio).is_file():
        raise WorkflowError(f"session {session_id} has no stored audio")

    should = settings.transcribe_on_upload if transcribe is None else transcribe
    result: dict[str, Any] = {"session_id": session_id, "status": session["status"]}
    if should and not transcript_path(session_id).is_file():
        try:
            result["transcription"] = transcribe_session(session_id)
        except Exception as exc:  # leave the session un-queued; surface the failure
            result["transcription_error"] = str(exc)

    # `transcribe=False` is an explicit "queue it as it is" from a caller that knows
    # what it is doing; otherwise a transcript is what makes the session queueable.
    ready = transcript_path(session_id).is_file() or not should
    result["queued"] = ready
    if not ready:
        result["hint"] = NOT_QUEUED_HINT
        return result

    with dbmod.cursor() as conn:
        dbmod.update_session(conn, session_id, status="pending")
    marker = queue_marker(session_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(dbmod.utcnow(), encoding="utf-8")
    result["status"] = "pending"
    result["hint"] = QUEUED_HINT_WORKLOG if session["mode"] == "worklog" else QUEUED_HINT
    return result


def pending_sessions() -> list[dict[str, Any]]:
    with dbmod.cursor() as conn:
        return dbmod.list_sessions(conn, status="pending")


def clear_queue_marker(session_id: int) -> None:
    marker = queue_marker(session_id)
    if marker.exists():
        marker.unlink()


# --------------------------------------------------------------------------- #
# feedback in (the skill's write-back path)
# --------------------------------------------------------------------------- #

FEEDBACK_REQUIRED = ("session_id", "scores")


def record_feedback(payload: dict[str, Any], *, markdown: str | None = None) -> dict[str, Any]:
    """Apply one session's feedback: score, word usage, SM-2 updates, status.

    `payload` is what `process-session` produces (see docs/commands.md). Everything
    numeric - the weighted overall, ease factors, intervals, mastery - is computed
    here so two runs of the skill can never disagree about the arithmetic.
    """
    missing = [k for k in FEEDBACK_REQUIRED if k not in payload]
    if missing:
        raise WorkflowError(f"feedback payload missing required keys: {missing}")

    session_id = int(payload["session_id"])
    scores = dict(payload["scores"] or {})
    unknown = set(scores) - set(dbmod.SCORE_DIMENSIONS)
    if unknown:
        raise WorkflowError(f"unknown score dimensions: {sorted(unknown)}")
    for dim, value in scores.items():
        if value is None:
            continue
        if not 0 <= float(value) <= 10:
            raise WorkflowError(f"score {dim}={value} is outside 0..10")

    summary: dict[str, Any] = {"session_id": session_id, "words_updated": [], "words_added": []}

    with dbmod.cursor() as conn:
        session = dbmod.get_session(conn, session_id)
        if session is None:
            raise WorkflowError(f"session {session_id} does not exist")
        if session["mode"] == "worklog":
            # Scoring a journal rewards performing over reporting (PRD-worklog 5.1).
            raise WorkflowError(
                f"session {session_id} is a worklog session - apply it with "
                "`ect worklog add`, not `ect feedback apply`"
            )
        if session["mode"] == "freeform":
            scores.pop("target_usage", None)  # N/A (PRD 9)

        summary["overall"] = dbmod.insert_score(conn, session_id, scores)
        summary["words_updated"] = _apply_reviews(
            conn, session_id, payload.get("target_words") or []
        )
        summary["words_added"] = _apply_new_words(conn, payload.get("new_words") or [])
        _apply_suggestions(conn, session["mode"], payload.get("suggestions") or [])

        path = feedback_path(session_id)
        if markdown is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(markdown, encoding="utf-8")

        dbmod.update_session(
            conn,
            session_id,
            status="processed",
            processed_at=dbmod.utcnow(),
            feedback_path=relpath(path) if path.is_file() else None,
        )

    clear_queue_marker(session_id)
    summary["status"] = "processed"
    stored = feedback_path(session_id)
    # None, not the path it would have had: the frontend renders whatever is on disk, so
    # reporting a path for a file that was never written hides the fact that the session
    # is processed with no feedback to show.
    summary["feedback_path"] = relpath(stored) if stored.is_file() else None
    return summary


def _apply_reviews(conn, session_id: int, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One SM-2 review per target word. Claude judged the usage; the schedule is ours."""
    updated: list[dict[str, Any]] = []
    for entry in entries:
        term = str(entry.get("term", "")).strip()
        if not term:
            continue
        used = bool(entry.get("used"))
        correct = bool(entry.get("used_correctly"))
        quality = entry.get("quality")
        quality = int(quality) if quality is not None else srs.grade_from_usage(used, correct)

        word_id = dbmod.add_word(conn, term=term, source="recommended", notes=entry.get("note"))
        word = conn.execute("SELECT * FROM words WHERE id = ?", (word_id,)).fetchone()
        state = srs.review(dict(word), quality)
        conn.execute(
            """UPDATE words
                  SET times_seen = times_seen + 1,
                      times_used_correctly = times_used_correctly + ?,
                      last_practiced = ?,
                      ease = ?, interval_days = ?, repetitions = ?,
                      due_date = ?, mastery = ?,
                      notes = COALESCE(?, notes)
                WHERE id = ?""",
            (
                1 if correct else 0,
                dbmod.today(),
                state.ease,
                state.interval_days,
                state.repetitions,
                state.due_date,
                state.mastery,
                entry.get("note"),
                word_id,
            ),
        )
        dbmod.record_word_usage(
            conn, word_id=word_id, session_id=session_id, used=used, used_correctly=correct
        )
        updated.append(
            {"term": term, "quality": quality, "due": state.due_date, "mastery": state.mastery}
        )
    return updated


def _apply_new_words(conn, entries: list[dict[str, Any]]) -> list[str]:
    added: list[str] = []
    for entry in entries:
        term = str(entry.get("term", "")).strip()
        if not term:
            continue
        dbmod.add_word(
            conn,
            term=term,
            kind=entry.get("kind"),
            meaning=entry.get("meaning"),
            example=entry.get("example"),
            source=entry.get("source") or "recommended",
            notes=entry.get("note"),
        )
        added.append(term)
    return added


def _apply_suggestions(conn, default_mode: str, entries: list[dict[str, Any]]) -> None:
    for entry in entries:
        if not entry.get("topic"):
            continue
        dbmod.add_suggestion(
            conn,
            mode=entry.get("mode") or default_mode,
            topic=entry["topic"],
            category=entry.get("category"),
            target_words=entry.get("target_words") or [],
            rationale=entry.get("rationale"),
        )


# --------------------------------------------------------------------------- #
# worklog (PRD-worklog: the journal's single write path)
# --------------------------------------------------------------------------- #

# Controlled competency vocabulary (PRD-worklog 6.1). Free-form tags fragment into
# synonyms and break tag-based retrieval; extending this tuple is a deliberate edit
# to the PRD, not a per-entry improvisation.
WORKLOG_TAGS = (
    "leadership",
    "ownership",
    "conflict",
    "ambiguity",
    "cross-team",
    "debugging",
    "design-tradeoff",
    "mentoring",
    "failure",
    "delivery",
    "influence",
)

_MONTH_RE = re.compile(r"\d{4}-\d{2}")


def _validate_iso_date(value: str, *, what: str) -> str:
    from datetime import date

    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError) as exc:
        raise WorkflowError(f"{what} must be an ISO date (YYYY-MM-DD), got {value!r}") from exc


def record_worklog_entry(
    *,
    entry_date: str,
    markdown: str,
    summary: str,
    projects: list[str] | None = None,
    tags: list[str] | None = None,
    session_id: int | None = None,
) -> dict[str, Any]:
    """File one day's journal entry and index it. Overwrites the day's file: merging
    a second same-day recording into the existing entry is judgement, so the skill
    reads the old entry, merges, and hands the combined markdown back here."""
    entry_date = _validate_iso_date(entry_date, what="entry date")
    if not markdown.strip():
        raise WorkflowError("the entry markdown is empty")
    if not (summary or "").strip():
        raise WorkflowError("a one-line summary is required - it is what listings show")
    unknown = [t for t in (tags or []) if t not in WORKLOG_TAGS]
    if unknown:
        raise WorkflowError(f"unknown worklog tags {unknown}; allowed: {', '.join(WORKLOG_TAGS)}")

    ensure_tree()
    path = worklog_daily_path(entry_date)
    path.write_text(markdown, encoding="utf-8")

    with dbmod.cursor() as conn:
        if session_id is not None:
            session = dbmod.get_session(conn, session_id)
            if session is None:
                raise WorkflowError(f"session {session_id} does not exist")
            if session["mode"] != "worklog":
                raise WorkflowError(
                    f"session {session_id} is a {session['mode']} session, not worklog"
                )
        entry_id = dbmod.upsert_worklog_entry(
            conn,
            entry_date=entry_date,
            path=relpath(path) or str(path),
            session_id=session_id,
            projects=projects,
            tags=tags,
            summary=summary.strip(),
        )
        if session_id is not None:
            # The daily entry is what the frontend renders for this session.
            dbmod.update_session(
                conn,
                session_id,
                status="processed",
                processed_at=dbmod.utcnow(),
                feedback_path=relpath(path),
            )
    if session_id is not None:
        clear_queue_marker(session_id)
    return {
        "entry_id": entry_id,
        "entry_date": entry_date,
        "path": relpath(path),
        "session_id": session_id,
        "status": "processed" if session_id is not None else None,
    }


def record_worklog_rollup(*, month: str, markdown: str) -> dict[str, Any]:
    if not _MONTH_RE.fullmatch(month or ""):
        raise WorkflowError(f"month must look like YYYY-MM, got {month!r}")
    if not markdown.strip():
        raise WorkflowError("the rollup markdown is empty")
    ensure_tree()
    path = worklog_rollup_path(month)
    path.write_text(markdown, encoding="utf-8")
    with dbmod.cursor() as conn:
        dbmod.upsert_worklog_rollup(conn, month=month, path=relpath(path) or str(path))
    return {"month": month, "path": relpath(path)}


def worklog_rollup_status() -> dict[str, Any]:
    """Which completed months have entries but no rollup yet. The current month is
    never 'missing' - it is still accumulating."""
    with dbmod.cursor() as conn:
        months = dbmod.worklog_months(conn)
        rolled = set(dbmod.worklog_rollup_months(conn))
    current = dbmod.today()[:7]
    return {
        "months_with_entries": months,
        "rollups": sorted(rolled),
        "missing": [m for m in months if m < current and m not in rolled],
    }
