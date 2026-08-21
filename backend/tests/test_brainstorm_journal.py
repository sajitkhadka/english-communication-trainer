"""Brainstorm (idea-dump, own write-back) and journal (self-finalising, no AI) modes,
plus the lean content-only brief they share with worklog, and mode-switching."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def services(data_dir: Path):
    from app import services as module

    return module


@pytest.fixture
def dbmod(data_dir: Path):
    from app import db as module

    return module


def _fake_payload(session_id: int, *, mode: str, topic: str | None, text: str) -> dict:
    return {
        "session_id": session_id,
        "mode": mode,
        "category": None,
        "topic": topic,
        "target_words": [],
        "audio": {"file": "x", "duration_sec": 12.0, "sample_rate": 16_000},
        "transcript": {"text": text, "sentences": [], "words": []},
        "speech": {
            "words_total": len(text.split()),
            "wpm_overall": 100,
            "wpm_speaking": 100,
            "speaking_sec": 12.0,
            "silence_sec": 0.0,
            "speech_ratio": 1.0,
            "sentence_count": 1,
            "avg_sentence_words": len(text.split()),
        },
        "pauses": {
            "count": 0,
            "per_minute": 0,
            "total_sec": 0,
            "longest_sec": 0,
            "mid_sentence_count": 0,
            "buckets": {},
            "items": [],
        },
        "fillers": {
            "textual": {
                "total": 0,
                "hard_total": 0,
                "ambiguous_total": 0,
                "per_minute": 0,
                "by_term": {},
                "items": [],
            },
            "acoustic": {"total": 0, "per_minute": 0, "note": "", "items": []},
            "combined_total": 0,
            "combined_per_minute": 0,
            "cross_check": {"total": 0, "per_minute": 0, "note": "", "items": []},
        },
        "target_word_hits": [],
        "meta": {
            "pipeline_version": 1,
            "model": "test",
            "compute_type": "test",
            "aligned": True,
            "language": "en",
            "vad": "silero",
            "parakeet_pass": False,
            "target_word_cross_check": None,
            "elapsed_sec": 0.1,
            "generated_at": "2026-08-21T00:00:00+00:00",
            "thresholds": {},
        },
    }


@pytest.fixture
def fake_transcribe(monkeypatch):
    """Stand in for the GPU pipeline: writes the same two artefacts `pipeline.runner.run`
    would (transcript.json + the plain-text sibling), so `_transcribe_session` sees a
    normal result without a real model load."""
    from app.paths import transcript_path, transcript_text_path

    def _run(session_id, audio_path, *, mode, topic, category, target_words, write=True):
        payload = _fake_payload(
            session_id, mode=mode, topic=topic, text="Talked about a few things today."
        )
        if write:
            transcript_path(session_id).parent.mkdir(parents=True, exist_ok=True)
            transcript_path(session_id).write_text(json.dumps(payload), encoding="utf-8")
            transcript_text_path(session_id).write_text(
                payload["transcript"]["text"], encoding="utf-8"
            )
        return payload

    import pipeline.runner as runner_module

    monkeypatch.setattr(runner_module, "run", _run)
    return _run


class TestBrainstormMode:
    def test_needs_no_topic_and_gets_a_dated_default(self, services, dbmod):
        session = services.create_session(mode="brainstorm", topic=None)
        assert session["mode"] == "brainstorm"
        assert session["topic"] == f"Brainstorm - {dbmod.today()}"

    def test_feedback_apply_refuses_brainstorm_sessions(self, services):
        session = services.create_session(mode="brainstorm", topic=None)
        with pytest.raises(services.WorkflowError, match="brainstorm add"):
            services.record_feedback({"session_id": session["id"], "scores": {"fluency": 5.0}})

    def test_record_brainstorm_entry_titles_the_file_and_completes_the_session(
        self, services, dbmod
    ):
        from app.paths import abspath

        session = services.create_session(mode="brainstorm", topic=None)
        sid = session["id"]
        result = services.record_brainstorm_entry(
            session_id=sid,
            markdown="## Ideas\n- a cli for X\n",
            title="A CLI For X",
            summary="Idea for a small CLI tool",
        )
        assert result["path"].endswith("a-cli-for-x.md")
        assert abspath(result["path"]).is_file()
        with dbmod.cursor() as conn:
            row = dbmod.get_session(conn, sid)
        assert row["status"] == "processed"
        assert row["title"] == "A CLI For X"
        assert row["summary"] == "Idea for a small CLI tool"
        assert row["feedback_path"] == result["path"]

    def test_record_brainstorm_entry_refuses_wrong_mode(self, services):
        session = services.create_session(mode="freeform", topic="t")
        with pytest.raises(services.WorkflowError, match="not brainstorm"):
            services.record_brainstorm_entry(session_id=session["id"], markdown="x")

    def test_record_brainstorm_entry_refuses_empty_markdown(self, services):
        session = services.create_session(mode="brainstorm", topic=None)
        with pytest.raises(services.WorkflowError, match="empty"):
            services.record_brainstorm_entry(session_id=session["id"], markdown="   ")


class TestJournalMode:
    def test_needs_no_topic_and_gets_a_dated_default(self, services, dbmod):
        session = services.create_session(mode="journal", topic=None)
        assert session["mode"] == "journal"
        assert session["topic"] == f"Journal - {dbmod.today()}"

    def test_enqueue_refuses_journal_sessions(self, services):
        session = services.create_session(mode="journal", topic=None)
        services.store_recording(session["id"], b"audio", suffix=".webm")
        with pytest.raises(services.WorkflowError, match="never queued"):
            services.enqueue(session["id"])

    def test_transcription_self_finalises_with_no_queue_marker(
        self, services, dbmod, fake_transcribe
    ):
        from app.paths import queue_marker, transcript_text_path

        session = services.create_session(mode="journal", topic=None)
        sid = session["id"]
        services.store_recording(sid, b"audio", suffix=".webm")

        result = services.transcribe_session(sid)
        assert result["status"] == "transcribed"

        with dbmod.cursor() as conn:
            row = dbmod.get_session(conn, sid)
        assert row["status"] == "processed"
        assert row["feedback_path"].endswith(f"{sid}.txt")
        assert not queue_marker(sid).is_file()
        assert (
            transcript_text_path(sid).read_text(encoding="utf-8")
            == "Talked about a few things today."
        )

    def test_journal_entry_renders_as_the_feedback_card_over_http(
        self, client, services, fake_transcribe
    ):
        session = services.create_session(mode="journal", topic=None)
        sid = session["id"]
        services.store_recording(sid, b"audio", suffix=".webm")
        services.transcribe_session(sid)

        detail = client.get(f"/api/sessions/{sid}").json()
        assert detail["status"] == "processed"
        assert detail["has_feedback"] is True
        assert detail["feedback_markdown"] == "Talked about a few things today."


class TestLeanBrief:
    def test_worklog_and_brainstorm_briefs_skip_measurements(
        self, services, dbmod, fake_transcribe
    ):
        from app.brief import brief_for_session

        session = services.create_session(mode="brainstorm", topic=None)
        sid = session["id"]
        services.store_recording(sid, b"audio", suffix=".webm")
        services.transcribe_session(sid)

        brief = brief_for_session(sid)
        assert "Talked about a few things today." in brief
        assert "Backend measurements" not in brief
        assert "Target-word usage" not in brief

    def test_coached_modes_keep_the_full_brief(self, services, dbmod, fake_transcribe):
        from app.brief import brief_for_session

        session = services.create_session(mode="freeform", topic="t")
        sid = session["id"]
        services.store_recording(sid, b"audio", suffix=".webm")
        services.transcribe_session(sid)

        brief = brief_for_session(sid)
        assert "Backend measurements" in brief


class TestModeSwitch:
    def test_moves_the_recording_and_invalidates_stale_analysis(self, services, dbmod):
        from app.paths import feedback_path, find_recording, transcript_path

        session = services.create_session(mode="freeform", topic=None)
        sid = session["id"]
        services.store_recording(sid, b"audio", suffix=".webm")
        transcript_path(sid).write_text("{}", encoding="utf-8")
        feedback_path(sid).write_text("stale", encoding="utf-8")

        updated = services.change_session_mode(sid, "worklog")
        assert updated["mode"] == "worklog"
        assert updated["status"] == "recorded"
        # No real topic to preserve - falls back to the same dated default as
        # `create_session` would have used.
        assert updated["topic"] == f"Worklog - {dbmod.today()}"
        assert not transcript_path(sid).exists()
        assert not feedback_path(sid).exists()
        assert find_recording(sid, "freeform") is None
        assert find_recording(sid, "worklog") is not None

    def test_a_real_topic_survives_the_switch(self, services):
        session = services.create_session(mode="freeform", topic="Explain the deploy process")
        updated = services.change_session_mode(session["id"], "worklog")
        assert updated["topic"] == "Explain the deploy process"

    def test_survives_a_source_file_still_open_elsewhere(self, services, monkeypatch):
        """Windows: a lingering reader (the frontend's <audio> element) can block
        deleting the old-mode file even though copying it succeeds fine. The switch
        must still complete - a leftover file costs disk space, not correctness."""
        from pathlib import Path

        from app.paths import find_recording

        session = services.create_session(mode="freeform", topic=None)
        sid = session["id"]
        services.store_recording(sid, b"audio", suffix=".webm")

        def _locked_unlink(self):
            raise PermissionError("[WinError 32] file in use")

        monkeypatch.setattr(Path, "unlink", _locked_unlink)

        updated = services.change_session_mode(sid, "worklog")
        assert updated["mode"] == "worklog"
        assert updated["status"] == "recorded"
        assert find_recording(sid, "worklog") is not None

    def test_refuses_once_processed(self, services):
        session = services.create_session(mode="brainstorm", topic=None)
        sid = session["id"]
        services.record_brainstorm_entry(session_id=sid, markdown="x", title="t")
        with pytest.raises(services.WorkflowError, match="already processed"):
            services.change_session_mode(sid, "journal")

    def test_refuses_unswitchable_targets(self, services):
        session = services.create_session(mode="freeform", topic="t")
        with pytest.raises(services.WorkflowError, match="can only switch"):
            services.change_session_mode(session["id"], "interview")
