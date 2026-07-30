"""Vocabulary + progress reads for the frontend."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from .. import db as dbmod
from ..models import WordOut

router = APIRouter(prefix="/api", tags=["vocabulary"])


def _mark_due(word: dict[str, Any], today: str) -> dict[str, Any]:
    due = word.get("due_date")
    word["is_due"] = bool(due is None or due <= today)
    return word


@router.get("/words", response_model=list[WordOut])
def list_words(
    sort: str = Query(default="recency", pattern="^(recency|frequency|mastery|alpha|due)$"),
    limit: int = Query(default=1000, ge=1, le=5000),
) -> Any:
    today = dbmod.today()
    with dbmod.cursor() as conn:
        return [_mark_due(w, today) for w in dbmod.list_words(conn, sort=sort, limit=limit)]


@router.get("/words/due", response_model=list[WordOut])
def due_words(limit: int = Query(default=15, ge=1, le=200)) -> Any:
    today = dbmod.today()
    with dbmod.cursor() as conn:
        return [_mark_due(w, today) for w in dbmod.due_words(conn, limit=limit)]


@router.get("/words/stats")
def word_stats() -> Any:
    with dbmod.cursor() as conn:
        return dbmod.word_stats(conn)


@router.get("/progress")
def progress(limit: int = Query(default=200, ge=1, le=1000)) -> Any:
    """Score history plus a light trend summary for the Progress page (PRD 11)."""
    with dbmod.cursor() as conn:
        history = dbmod.score_history(conn, limit=limit)
        stats = dbmod.word_stats(conn)
    overalls = [h["overall"] for h in history if h.get("overall") is not None]
    recent = overalls[-5:]
    earlier = overalls[:-5]
    return {
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
        "dimensions": [d for d in dbmod.SCORE_DIMENSIONS],
        "weights": dbmod.SCORE_WEIGHTS,
    }
