"""The learning-notes wiki: read and hand-edit `data/learning-notes.md`.

The markdown file is the source of truth, not a DB row - it is prose, it is read whole,
and Claude edits it too (`/process-session` step 8). That shared ownership is why a save
carries the version the editor started from: see `services.write_notes`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from .. import services
from ..models import NotesOut, NotesUpdate

router = APIRouter(prefix="/api", tags=["notes"])


@router.get("/notes", response_model=NotesOut)
def get_notes() -> Any:
    return services.read_notes()


@router.put("/notes", response_model=NotesOut)
def put_notes(body: NotesUpdate) -> Any:
    try:
        return services.write_notes(body.markdown, version=body.version)
    except services.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except services.WorkflowError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
