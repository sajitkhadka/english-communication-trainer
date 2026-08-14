"""The session lifecycle and the feedback write-back contract, over a temp database."""

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


class TestSessionCreation:
    def test_creates_row_and_prompt_file(self, services, dbmod):
        session = services.create_session(
            mode="recommended", topic="Describe a bottleneck", target_words=["leverage"]
        )
        assert session["status"] == "awaiting_recording"

        from app.paths import prompt_path

        payload = json.loads(prompt_path(session["id"]).read_text(encoding="utf-8"))
        assert payload["topic"] == "Describe a bottleneck"
        assert payload["target_words"] == ["leverage"]

    def test_target_words_enter_the_corpus_immediately(self, services, dbmod):
        services.create_session(mode="recommended", topic="t", target_words=["de-risk"])
        with dbmod.cursor() as conn:
            assert dbmod.get_word_by_term(conn, "de-risk") is not None

    def test_recommended_requires_a_topic(self, services):
        with pytest.raises(services.WorkflowError, match="need a topic"):
            services.create_session(mode="recommended", topic=None)

    def test_freeform_does_not(self, services):
        assert services.create_session(mode="freeform", topic=None)["mode"] == "freeform"

    def test_unknown_mode_is_rejected(self, services):
        with pytest.raises(services.WorkflowError, match="unknown mode"):
            services.create_session(mode="karaoke", topic="t")


class TestRecording:
    def test_storing_audio_marks_the_session_recorded(self, services):
        session = services.create_session(mode="freeform", topic="t")
        stored = services.store_recording(session["id"], b"RIFFfake", suffix=".webm")
        assert stored["status"] == "recorded"
        assert stored["audio_path"].endswith(".webm")

    def test_re_recording_discards_the_old_analysis(self, services):
        from app.paths import feedback_path, transcript_path

        session = services.create_session(mode="freeform", topic="t")
        sid = session["id"]
        services.store_recording(sid, b"one", suffix=".webm")
        transcript_path(sid).write_text("{}", encoding="utf-8")
        feedback_path(sid).write_text("stale", encoding="utf-8")

        services.store_recording(sid, b"two", suffix=".webm")
        assert not transcript_path(sid).exists()
        assert not feedback_path(sid).exists()

    def test_cannot_record_against_a_missing_session(self, services):
        with pytest.raises(services.WorkflowError, match="does not exist"):
            services.store_recording(999, b"x")


class TestQueue:
    def test_enqueue_flags_pending_and_writes_a_marker(self, services, monkeypatch):
        from app.config import settings
        from app.paths import queue_marker

        monkeypatch.setattr(settings, "transcribe_on_upload", False)
        session = services.create_session(mode="freeform", topic="t")
        services.store_recording(session["id"], b"audio", suffix=".webm")

        result = services.enqueue(session["id"])
        assert result["status"] == "pending"
        assert queue_marker(session["id"]).is_file()
        assert [s["id"] for s in services.pending_sessions()] == [session["id"]]

    def test_cannot_queue_a_session_with_no_recording(self, services):
        session = services.create_session(mode="freeform", topic="t")
        with pytest.raises(services.WorkflowError, match="no recording"):
            services.enqueue(session["id"])

    def test_a_failed_transcription_leaves_the_session_unqueued(self, services, monkeypatch):
        """ "Queued for Claude" has to mean there is something for Claude to read."""
        from app.config import settings
        from app.paths import queue_marker

        monkeypatch.setattr(settings, "transcribe_on_upload", True)
        session = services.create_session(mode="freeform", topic="t")
        # Not decodable audio: the pipeline fails at ffmpeg, before any model load.
        services.store_recording(session["id"], b"not-audio", suffix=".webm")

        result = services.enqueue(session["id"])
        assert result["queued"] is False
        assert result["status"] == "recorded"
        assert result["transcription_error"]
        assert not queue_marker(session["id"]).is_file()
        assert services.pending_sessions() == []

    def test_a_second_transcription_is_refused_while_one_runs(self, services):
        """One GPU: a concurrent run used to take the whole process down with it."""
        session = services.create_session(mode="freeform", topic="t")
        services.store_recording(session["id"], b"not-audio", suffix=".webm")

        assert services._gpu_lock.acquire(blocking=False)
        try:
            with pytest.raises(services.WorkflowError, match="already running"):
                services.transcribe_session(session["id"])
        finally:
            services._gpu_lock.release()


class TestRecordFeedback:
    @pytest.fixture
    def recorded(self, services, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "transcribe_on_upload", False)
        session = services.create_session(
            mode="recommended",
            topic="Describe a bottleneck",
            target_words=["leverage", "mitigate"],
        )
        services.store_recording(session["id"], b"audio", suffix=".webm")
        services.enqueue(session["id"])
        return session["id"]

    def base_payload(self, session_id: int) -> dict:
        return {
            "session_id": session_id,
            "scores": {
                "vocab_range": 6.0,
                "filler_density": 5.0,
                "fluency": 6.0,
                "grammar": 7.0,
                "structure": 6.0,
                "coherence": 7.0,
                "target_usage": 5.0,
            },
            "target_words": [
                {"term": "leverage", "used": True, "used_correctly": True},
                {"term": "mitigate", "used": False, "used_correctly": False},
            ],
            "new_words": [
                {
                    "term": "de-risk",
                    "kind": "word",
                    "meaning": "reduce risk",
                    "example": "We de-risked it.",
                    "source": "recommended",
                }
            ],
        }

    def test_applies_score_and_marks_processed(self, services, dbmod, recorded):
        summary = services.record_feedback(self.base_payload(recorded), markdown="# Feedback")
        assert summary["status"] == "processed"
        assert summary["overall"] == pytest.approx(6.07, abs=0.01)

        with dbmod.cursor() as conn:
            session = dbmod.get_session(conn, recorded)
            assert session["status"] == "processed"
            assert session["processed_at"] is not None
            assert dbmod.get_score(conn, recorded)["overall"] == summary["overall"]

    def test_writes_the_markdown_and_clears_the_queue_marker(self, services, recorded):
        from app.paths import feedback_path, queue_marker

        services.record_feedback(self.base_payload(recorded), markdown="# Feedback\n\nGood.")
        assert feedback_path(recorded).read_text(encoding="utf-8").startswith("# Feedback")
        assert not queue_marker(recorded).exists()

    def test_correct_use_pushes_the_word_out_and_non_use_pulls_it_back(
        self, services, dbmod, recorded
    ):
        services.record_feedback(self.base_payload(recorded))
        with dbmod.cursor() as conn:
            used_well = dbmod.get_word_by_term(conn, "leverage")
            never_used = dbmod.get_word_by_term(conn, "mitigate")

        assert used_well["repetitions"] == 1
        assert used_well["times_used_correctly"] == 1
        assert used_well["mastery"] > never_used["mastery"]
        assert never_used["repetitions"] == 0
        assert never_used["times_seen"] == 1, "a word that was not used still counts as reviewed"

    def test_new_words_are_added(self, services, dbmod, recorded):
        services.record_feedback(self.base_payload(recorded))
        with dbmod.cursor() as conn:
            word = dbmod.get_word_by_term(conn, "de-risk")
        assert word["meaning"] == "reduce risk"

    def test_usage_is_linked_to_the_session(self, services, dbmod, recorded):
        services.record_feedback(self.base_payload(recorded))
        with dbmod.cursor() as conn:
            usage = {row["term"]: row for row in dbmod.words_for_session(conn, recorded)}
        assert usage["leverage"]["used_correctly"] == 1
        assert usage["mitigate"]["used"] == 0

    def test_freeform_drops_target_usage(self, services, dbmod, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "transcribe_on_upload", False)
        session = services.create_session(mode="freeform", topic="t")
        services.store_recording(session["id"], b"audio", suffix=".webm")
        payload = self.base_payload(session["id"])
        payload["target_words"] = []

        services.record_feedback(payload)
        with dbmod.cursor() as conn:
            score = dbmod.get_score(conn, session["id"])
        assert score["target_usage"] is None
        assert score["overall"] == pytest.approx(6.16, abs=0.01)

    def test_reprocessing_replaces_rather_than_duplicates_word_usage(
        self, services, dbmod, recorded
    ):
        services.record_feedback(self.base_payload(recorded))
        services.record_feedback(self.base_payload(recorded))
        with dbmod.cursor() as conn:
            rows = dbmod.words_for_session(conn, recorded)
        assert len(rows) == 2

    def test_rejects_an_unknown_score_dimension(self, services, recorded):
        payload = self.base_payload(recorded)
        payload["scores"]["charisma"] = 9.0
        with pytest.raises(services.WorkflowError, match="unknown score dimensions"):
            services.record_feedback(payload)

    def test_rejects_out_of_range_scores(self, services, recorded):
        payload = self.base_payload(recorded)
        payload["scores"]["grammar"] = 11.0
        with pytest.raises(services.WorkflowError, match=r"outside 0\.\.10"):
            services.record_feedback(payload)

    def test_rejects_a_payload_with_no_scores_key(self, services, recorded):
        with pytest.raises(services.WorkflowError, match="missing required keys"):
            services.record_feedback({"session_id": recorded})

    def test_rejects_an_unknown_session(self, services):
        with pytest.raises(services.WorkflowError, match="does not exist"):
            services.record_feedback({"session_id": 999, "scores": {"grammar": 5.0}})

    def test_suggestions_are_stored(self, services, dbmod, recorded):
        payload = self.base_payload(recorded)
        payload["suggestions"] = [
            {"mode": "interview", "topic": "Tell me about a migration", "category": "behavioural"}
        ]
        services.record_feedback(payload)
        with dbmod.cursor() as conn:
            assert len(dbmod.list_suggestions(conn)) == 1
