"""Request/response shapes for the REST API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

Mode = Literal["recommended", "freeform", "interview", "worklog", "brainstorm", "journal"]
Status = Literal["awaiting_recording", "recorded", "pending", "processed"]


class SessionCreate(BaseModel):
    mode: Mode = "freeform"
    topic: str | None = None
    category: str | None = None
    target_words: list[str] = Field(default_factory=list)
    notes: str | None = None
    # Set only by `ect agent` when draining a capture from the relay inbox (ADR 0006).
    # Re-posting the same uid returns the existing session instead of a duplicate.
    external_uid: str | None = None

    @field_validator("topic", "category", "notes")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class SessionOut(BaseModel):
    id: int
    mode: str
    category: str | None = None
    topic: str | None = None
    target_words: list[str] = Field(default_factory=list)
    status: str
    audio_path: str | None = None
    transcript_path: str | None = None
    feedback_path: str | None = None
    created_at: str | None = None
    processed_at: str | None = None
    transcribe_status: str | None = None
    transcribe_error: str | None = None
    duration_sec: float | None = None
    notes: str | None = None
    title: str | None = None
    summary: str | None = None
    external_uid: str | None = None
    score: dict[str, Any] | None = None
    has_audio: bool = False
    has_transcript: bool = False
    has_feedback: bool = False


class SessionDetail(SessionOut):
    feedback_markdown: str | None = None
    target_words_detail: list[dict[str, Any]] = Field(default_factory=list)


class SessionModeUpdate(BaseModel):
    mode: Literal["freeform", "worklog", "brainstorm", "journal"]


class WordOut(BaseModel):
    id: int
    term: str
    kind: str | None = None
    meaning: str | None = None
    example: str | None = None
    first_seen: str | None = None
    last_practiced: str | None = None
    times_seen: int = 0
    times_used_correctly: int = 0
    ease: float = 2.5
    interval_days: int = 0
    due_date: str | None = None
    mastery: float = 0.0
    source: str | None = None
    repetitions: int = 0
    notes: str | None = None
    is_due: bool = False


class NotesOut(BaseModel):
    markdown: str
    path: str | None = None
    # Opaque to the client: hand it back on save so a concurrent `/process-session`
    # edit is caught instead of overwritten (services.write_notes).
    version: str


class NotesUpdate(BaseModel):
    markdown: str
    version: str | None = None


class SuggestionOut(BaseModel):
    id: int
    mode: str
    category: str | None = None
    topic: str
    target_words: list[str] = Field(default_factory=list)
    rationale: str | None = None
    created_at: str | None = None
    consumed_by: int | None = None


class SuggestionRequestCreate(BaseModel):
    mode: Mode = "recommended"
    category: str | None = None


class ProcessResponse(BaseModel):
    session_id: int
    status: str
    # False when the recording could not be transcribed: the session stays `recorded`
    # rather than claiming to be queued for a Claude run that has nothing to read.
    queued: bool = True
    transcription: dict[str, Any] | None = None
    transcription_error: str | None = None
    hint: str = (
        "Flagged for Claude. Run `/process-session` in the Claude Code console to "
        "generate feedback."
    )
