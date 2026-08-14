"""Session CRUD, audio upload, transcript/feedback reads, and the process queue."""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse

from .. import db as dbmod
from .. import services
from ..brief import brief_for_session
from ..models import ProcessResponse, SessionCreate, SessionDetail, SessionOut
from ..paths import abspath, feedback_path, find_recording, prompt_path, transcript_path

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

# MediaRecorder container -> file extension. Anything unknown is stored as .bin and
# still decodes, because ffmpeg sniffs the container rather than trusting the name.
SUFFIX_BY_TYPE = {
    "audio/webm": ".webm",
    "video/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "video/mp4": ".mp4",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
}


def _feedback_file(session: dict[str, Any]) -> Path:
    """The stored path wins: worklog sessions link their daily journal entry, which
    lives under data/worklog/, not at the canonical data/feedback/<id>.md."""
    stored = abspath(session.get("feedback_path"))
    if stored and stored.is_file():
        return stored
    return feedback_path(session["id"])


def _decorate(session: dict[str, Any], conn) -> dict[str, Any]:
    sid = session["id"]
    audio = abspath(session.get("audio_path")) or find_recording(sid, session["mode"])
    session["has_audio"] = bool(audio and Path(audio).is_file())
    session["has_transcript"] = transcript_path(sid).is_file()
    session["has_feedback"] = _feedback_file(session).is_file()
    session["score"] = dbmod.get_score(conn, sid)
    return session


@router.post("", response_model=SessionOut, status_code=201)
def create_session(body: SessionCreate) -> Any:
    try:
        session = services.create_session(
            mode=body.mode,
            topic=body.topic,
            category=body.category,
            target_words=body.target_words,
            notes=body.notes,
        )
    except services.WorkflowError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with dbmod.cursor() as conn:
        return _decorate(session, conn)


@router.get("", response_model=list[SessionOut])
def list_sessions(
    mode: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> Any:
    with dbmod.cursor() as conn:
        rows = dbmod.list_sessions(conn, mode=mode, status=status, limit=limit)
        return [_decorate(r, conn) for r in rows]


@router.get("/{session_id}", response_model=SessionDetail)
def get_session(session_id: int) -> Any:
    with dbmod.cursor() as conn:
        session = dbmod.get_session(conn, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"session {session_id} not found")
        _decorate(session, conn)
        detail = dict(session)
        detail["target_words_detail"] = [
            {
                "term": term,
                **{
                    k: v
                    for k, v in (dbmod.get_word_by_term(conn, term) or {}).items()
                    if k in ("kind", "meaning", "example", "mastery", "due_date")
                },
            }
            for term in session.get("target_words") or []
        ]
    path = _feedback_file(session)
    detail["feedback_markdown"] = path.read_text(encoding="utf-8") if path.is_file() else None
    return detail


@router.delete("/{session_id}", status_code=204)
def delete_session(session_id: int) -> None:
    with dbmod.cursor() as conn:
        session = dbmod.get_session(conn, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"session {session_id} not found")
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    audio = abspath(session.get("audio_path")) or find_recording(session_id, session["mode"])
    for path in (
        audio,
        transcript_path(session_id),
        feedback_path(session_id),
        prompt_path(session_id),
    ):
        if path and Path(path).is_file():
            Path(path).unlink()
    services.clear_queue_marker(session_id)


@router.post("/{session_id}/recording", response_model=SessionOut)
async def upload_recording(session_id: int, file: UploadFile = File(...)) -> Any:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="uploaded recording is empty")
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    suffix = SUFFIX_BY_TYPE.get(content_type)
    if suffix is None and file.filename:
        suffix = Path(file.filename).suffix or None
    try:
        session = services.store_recording(session_id, data, suffix=suffix or ".bin")
    except services.WorkflowError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    with dbmod.cursor() as conn:
        return _decorate(session, conn)


@router.get("/{session_id}/audio")
def get_audio(session_id: int) -> FileResponse:
    with dbmod.cursor() as conn:
        session = dbmod.get_session(conn, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    audio = abspath(session.get("audio_path")) or find_recording(session_id, session["mode"])
    if not audio or not Path(audio).is_file():
        raise HTTPException(status_code=404, detail="no recording stored for this session")
    media_type = mimetypes.guess_type(str(audio))[0] or "application/octet-stream"
    return FileResponse(audio, media_type=media_type, filename=Path(audio).name)


@router.get("/{session_id}/transcript")
def get_transcript(session_id: int) -> Any:
    path = transcript_path(session_id)
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail="not transcribed yet - press Process, or POST /transcribe",
        )
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/{session_id}/brief", response_class=PlainTextResponse)
def get_brief(session_id: int, max_sentences: int | None = None) -> str:
    """The slim, annotated view the process-session skill reads."""
    try:
        return brief_for_session(session_id, max_sentences=max_sentences)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{session_id}/feedback", response_class=PlainTextResponse)
def get_feedback(session_id: int) -> str:
    with dbmod.cursor() as conn:
        session = dbmod.get_session(conn, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    path = _feedback_file(session)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="no feedback yet for this session")
    return path.read_text(encoding="utf-8")


@router.get("/{session_id}/prompt")
def get_prompt(session_id: int) -> Any:
    path = prompt_path(session_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="no prompt file for this session")
    return json.loads(path.read_text(encoding="utf-8"))


@router.post("/{session_id}/process", response_model=ProcessResponse)
def process(session_id: int) -> Any:
    """PRD 6.3: flag intent. Claude is pulled by the user, never pushed by the app."""
    try:
        return services.enqueue(session_id)
    except services.WorkflowError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{session_id}/transcribe")
def transcribe(session_id: int, force: bool = False) -> Any:
    try:
        return services.transcribe_session(session_id, force=force)
    except services.WorkflowError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"transcription failed: {exc}") from exc
