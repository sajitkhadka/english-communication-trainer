"""The read-only snapshot the relay serves while the PC is off (ADR 0006, §3).

One-way and derived. `ect agent` fetches this from the local API on a timer, hashes
it, and pushes it to the relay only when the hash changes - so a `/process-session`
run, a frontend edit and a CLI write are all captured identically without hooking any
of them. The relay never writes into it.

That "never writes" is load-bearing rather than tidy. `services.write_notes` has an
optimistic-concurrency contract (load a `version`, hand it back on save, get a 409 if
someone else moved first). A writable replica would make that a distributed problem
with no obviously correct merge; a replica that refuses to be written cannot.

**Lossy by construction.** No audio, no transcripts, no per-word timings, and feedback
markdown only for the most recent `digest_feedback_horizon` sessions. Offline history
is for reading what was said about a session, not for replaying it.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from . import db as dbmod
from . import services
from .config import settings

# Everything the relay can answer offline is assembled here. Anything absent from this
# dict is a route the relay must 503 rather than guess at.
SCHEMA_VERSION = 1


def _canonical(payload: dict[str, Any]) -> str:
    """Stable JSON for hashing: sorted keys, no whitespace, no volatile fields."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def digest_version(payload: dict[str, Any]) -> str:
    """Content hash of everything except the timestamp.

    `generated_at` changes on every build by definition, so hashing it would make the
    agent push a fresh snapshot on every single poll - which is exactly the traffic
    the hash exists to avoid.
    """
    body = {k: v for k, v in payload.items() if k not in ("generated_at", "version")}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()[:16]


def build_digest(*, feedback_horizon: int | None = None) -> dict[str, Any]:
    """Assemble the snapshot. Pure reads; safe to call as often as you like."""
    horizon = settings.digest_feedback_horizon if feedback_horizon is None else feedback_horizon
    today = dbmod.today()

    with dbmod.cursor() as conn:
        sessions = [
            services.decorate_session(row, conn) for row in dbmod.list_sessions(conn, limit=1000)
        ]
        words = dbmod.list_words(conn, sort="recency", limit=5000)
        due = dbmod.due_words(conn, limit=200)
        stats = dbmod.word_stats(conn)
        history = dbmod.score_history(conn, limit=1000)
        suggestions = dbmod.list_suggestions(conn)
        pending = [s for s in sessions if s["status"] == "pending"]

        # `sessions` is newest-first (list_sessions orders by id DESC), so the horizon
        # is simply the head of the list.
        details: dict[str, Any] = {}
        for session in sessions[:horizon]:
            path = services.feedback_file(session)
            details[str(session["id"])] = {
                "feedback_markdown": (path.read_text(encoding="utf-8") if path.is_file() else None),
                "target_words_detail": [
                    {
                        "term": term,
                        **{
                            k: v
                            for k, v in (dbmod.get_word_by_term(conn, term) or {}).items()
                            if k in ("kind", "meaning", "example", "mastery", "due_date")
                        },
                    }
                    for term in session.get("target_words") or []
                ],
            }

    for word in words:
        word["is_due"] = bool(word.get("due_date") is None or word["due_date"] <= today)
    for word in due:
        word["is_due"] = True

    overalls = [h["overall"] for h in history if h.get("overall") is not None]
    recent, earlier = overalls[-5:], overalls[:-5]

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "feedback_horizon": horizon,
        "health": {
            "ok": True,
            "db": str(settings.db_path),
            "model": settings.whisper_model,
            "compute_type": settings.compute_type,
            "device": settings.device,
            "pending_sessions": len(pending),
            "sessions_by_mode": {
                mode: sum(1 for s in sessions if s["mode"] == mode)
                for mode in (
                    "recommended",
                    "freeform",
                    "interview",
                    "worklog",
                    "brainstorm",
                    "journal",
                )
            },
        },
        "sessions": sessions,
        "session_details": details,
        "notes": services.read_notes(),
        "words": words,
        "words_due": due,
        "word_stats": stats,
        "suggestions": suggestions,
        "queue": {
            "pending": pending,
            "count": len(pending),
            "hint": (
                "Read-only snapshot: the PC is offline. Queued sessions are drained by "
                "running `/process-queue` in the Claude Code console once it is back."
            ),
        },
        "progress": {
            "history": history,
            "sessions_scored": len(overalls),
            "latest": overalls[-1] if overalls else None,
            "best": max(overalls) if overalls else None,
            "average": round(sum(overalls) / len(overalls), 2) if overalls else None,
            "recent_average": round(sum(recent) / len(recent), 2) if recent else None,
            "delta_vs_earlier": (
                round(sum(recent) / len(recent) - sum(earlier) / len(earlier), 2)
                if recent and earlier
                else None
            ),
            "vocabulary": stats,
            "dimensions": list(dbmod.SCORE_DIMENSIONS),
            "weights": dbmod.SCORE_WEIGHTS,
        },
    }
    payload["version"] = digest_version(payload)
    payload["generated_at"] = dbmod.utcnow()
    return payload
