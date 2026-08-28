"""Health, queue view, suggestions, and GPU diagnostics."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from .. import db as dbmod
from .. import services
from ..config import settings
from ..models import SuggestionOut, SuggestionRequestCreate

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health() -> Any:
    with dbmod.cursor() as conn:
        pending = len(dbmod.list_sessions(conn, status="pending"))
        counts = {
            mode: len(dbmod.list_sessions(conn, mode=mode, limit=1000))
            for mode in ("recommended", "freeform", "interview", "worklog", "brainstorm", "journal")
        }
    return {
        "ok": True,
        "db": str(settings.db_path),
        "model": settings.whisper_model,
        "compute_type": settings.compute_type,
        "device": settings.device,
        "pending_sessions": pending,
        "sessions_by_mode": counts,
    }


@router.get("/doctor")
def doctor() -> Any:
    """Is the GPU stack actually usable? Loads nothing heavy."""
    from pipeline.gpu import gpu_report

    report = gpu_report()
    report["ffmpeg"] = _ffmpeg_present()
    return report


def _ffmpeg_present() -> bool:
    import shutil

    return shutil.which("ffmpeg") is not None


@router.get("/digest")
def digest(horizon: int | None = None) -> Any:
    """The offline snapshot `ect agent` mirrors to the relay (ADR 0006, app/digest.py).

    Assembled here rather than in the agent so the agent needs no database access and
    no knowledge of the schema: it fetches this, compares `version` with what it last
    pushed, and forwards it only when that moved.
    """
    from ..digest import build_digest

    return build_digest(feedback_horizon=horizon)


@router.get("/queue")
def queue() -> Any:
    """Sessions waiting on a Claude run (PRD 6.3, Phase 2 queue view)."""
    sessions = services.pending_sessions()
    return {
        "pending": sessions,
        "count": len(sessions),
        "hint": (
            "Run `/process-session` (practice), `/log-work` (worklog), or "
            "`/process-brainstorm` (brainstorm) in the Claude Code console to drain "
            "this queue. `journal` sessions never appear here - they finalise "
            "themselves at transcription."
        ),
    }


@router.get("/suggestions", response_model=list[SuggestionOut])
def suggestions(include_used: bool = False) -> Any:
    with dbmod.cursor() as conn:
        return dbmod.list_suggestions(conn, include_used=include_used)


@router.post("/suggestions/requests", status_code=201)
def request_suggestions(body: SuggestionRequestCreate) -> Any:
    with dbmod.cursor() as conn:
        request_id = dbmod.add_suggestion_request(conn, mode=body.mode, category=body.category)
    return {
        "id": request_id,
        "status": "open",
        "hint": "Run `/generate-topic` in the Claude Code console to fulfil this request.",
    }


@router.post("/sessions/{session_id}/use-suggestion/{suggestion_id}")
def use_suggestion(session_id: int, suggestion_id: int) -> Any:
    with dbmod.cursor() as conn:
        row = conn.execute("SELECT id FROM suggestions WHERE id = ?", (suggestion_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="suggestion not found")
        conn.execute(
            "UPDATE suggestions SET consumed_by = ? WHERE id = ?", (session_id, suggestion_id)
        )
    return {"suggestion_id": suggestion_id, "consumed_by": session_id}
