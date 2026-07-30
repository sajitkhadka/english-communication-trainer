"""HTTP surface, against a temp database. No GPU: transcription is stubbed where the
endpoint would reach for it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


class TestHealth:
    def test_health_reports_readiness(self, client):
        body = client.get("/api/health").json()
        assert body["ok"] is True
        assert body["pending_sessions"] == 0

    def test_root_points_at_the_docs(self, client):
        assert client.get("/").json()["health"] == "/api/health"


class TestSessions:
    def test_create_and_fetch(self, client):
        created = client.post(
            "/api/sessions",
            json={"mode": "recommended", "topic": "Describe a bottleneck",
                  "target_words": ["leverage"]},
        )
        assert created.status_code == 201
        session_id = created.json()["id"]

        detail = client.get(f"/api/sessions/{session_id}").json()
        assert detail["topic"] == "Describe a bottleneck"
        assert detail["target_words"] == ["leverage"]
        assert detail["has_audio"] is False
        assert detail["target_words_detail"][0]["term"] == "leverage"

    def test_blank_topic_becomes_null(self, client):
        body = client.post("/api/sessions", json={"mode": "freeform", "topic": "   "}).json()
        assert body["topic"] is None

    def test_recommended_without_a_topic_is_rejected(self, client):
        assert client.post("/api/sessions", json={"mode": "recommended"}).status_code == 400

    def test_unknown_mode_is_rejected_by_validation(self, client):
        assert client.post("/api/sessions", json={"mode": "karaoke"}).status_code == 422

    def test_list_filters_by_mode(self, client):
        client.post("/api/sessions", json={"mode": "freeform", "topic": "a"})
        client.post("/api/sessions", json={"mode": "interview", "topic": "b"})
        assert len(client.get("/api/sessions?mode=interview").json()) == 1

    def test_missing_session_is_404(self, client):
        assert client.get("/api/sessions/999").status_code == 404

    def test_delete_removes_the_session_and_its_files(self, client, data_dir: Path):
        session_id = client.post(
            "/api/sessions", json={"mode": "freeform", "topic": "t"}
        ).json()["id"]
        client.post(
            f"/api/sessions/{session_id}/recording",
            files={"file": ("clip.webm", b"audio-bytes", "audio/webm")},
        )
        assert client.delete(f"/api/sessions/{session_id}").status_code == 204
        assert client.get(f"/api/sessions/{session_id}").status_code == 404
        assert list((data_dir / "recordings" / "freeform").glob("*")) == []


class TestRecordingUpload:
    @pytest.fixture
    def session_id(self, client):
        return client.post("/api/sessions", json={"mode": "freeform", "topic": "t"}).json()["id"]

    def test_upload_marks_recorded_and_keeps_the_container(self, client, session_id, data_dir):
        body = client.post(
            f"/api/sessions/{session_id}/recording",
            files={"file": ("clip.webm", b"audio-bytes", "audio/webm")},
        ).json()
        assert body["status"] == "recorded"
        assert body["has_audio"] is True
        assert (data_dir / "recordings" / "freeform" / f"{session_id}.webm").is_file()

    def test_mp4_from_safari_is_stored_as_m4a(self, client, session_id, data_dir):
        client.post(
            f"/api/sessions/{session_id}/recording",
            files={"file": ("clip", b"audio-bytes", "audio/mp4")},
        )
        assert (data_dir / "recordings" / "freeform" / f"{session_id}.m4a").is_file()

    def test_empty_upload_is_rejected(self, client, session_id):
        response = client.post(
            f"/api/sessions/{session_id}/recording",
            files={"file": ("clip.webm", b"", "audio/webm")},
        )
        assert response.status_code == 400

    def test_audio_can_be_streamed_back(self, client, session_id):
        client.post(
            f"/api/sessions/{session_id}/recording",
            files={"file": ("clip.webm", b"audio-bytes", "audio/webm")},
        )
        assert client.get(f"/api/sessions/{session_id}/audio").content == b"audio-bytes"

    def test_audio_is_404_before_any_upload(self, client, session_id):
        assert client.get(f"/api/sessions/{session_id}/audio").status_code == 404


class TestProcessQueue:
    @pytest.fixture
    def recorded_id(self, client, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "transcribe_on_upload", False)
        session_id = client.post(
            "/api/sessions", json={"mode": "freeform", "topic": "t"}
        ).json()["id"]
        client.post(
            f"/api/sessions/{session_id}/recording",
            files={"file": ("clip.webm", b"audio-bytes", "audio/webm")},
        )
        return session_id

    def test_process_flags_pending_and_explains_the_handoff(self, client, recorded_id):
        body = client.post(f"/api/sessions/{recorded_id}/process").json()
        assert body["status"] == "pending"
        assert body["queued"] is True
        assert "/process-session" in body["hint"]

    def test_process_does_not_queue_when_transcription_fails(self, client, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "transcribe_on_upload", True)
        session_id = client.post(
            "/api/sessions", json={"mode": "freeform", "topic": "t"}
        ).json()["id"]
        client.post(
            f"/api/sessions/{session_id}/recording",
            files={"file": ("clip.webm", b"not-audio", "audio/webm")},
        )
        body = client.post(f"/api/sessions/{session_id}/process").json()
        assert body["queued"] is False
        assert body["status"] == "recorded"
        assert client.get("/api/queue").json()["count"] == 0

    def test_queue_lists_it(self, client, recorded_id):
        client.post(f"/api/sessions/{recorded_id}/process")
        queue = client.get("/api/queue").json()
        assert queue["count"] == 1
        assert queue["pending"][0]["id"] == recorded_id

    def test_cannot_process_a_session_with_no_recording(self, client):
        session_id = client.post(
            "/api/sessions", json={"mode": "freeform", "topic": "t"}
        ).json()["id"]
        assert client.post(f"/api/sessions/{session_id}/process").status_code == 409

    def test_transcript_is_409_until_the_pipeline_has_run(self, client, recorded_id):
        assert client.get(f"/api/sessions/{recorded_id}/transcript").status_code == 404
        assert client.get(f"/api/sessions/{recorded_id}/brief").status_code == 409


class TestFeedbackAndBrief:
    @pytest.fixture
    def processed(self, client, data_dir: Path, monkeypatch):
        from app.config import settings
        from app import services
        from app.paths import transcript_path

        monkeypatch.setattr(settings, "transcribe_on_upload", False)
        session_id = client.post(
            "/api/sessions",
            json={"mode": "recommended", "topic": "Bottlenecks", "target_words": ["leverage"]},
        ).json()["id"]
        client.post(
            f"/api/sessions/{session_id}/recording",
            files={"file": ("clip.webm", b"audio", "audio/webm")},
        )
        transcript_path(session_id).write_text(
            json.dumps(FAKE_TRANSCRIPT | {"session_id": session_id}), encoding="utf-8"
        )
        services.record_feedback(
            {
                "session_id": session_id,
                "scores": {"vocab_range": 6.0, "grammar": 7.0},
                "target_words": [{"term": "leverage", "used": True, "used_correctly": True}],
            },
            markdown="# Feedback\n\nSolid answer.",
        )
        return session_id

    def test_feedback_markdown_is_served(self, client, processed):
        assert "Solid answer" in client.get(f"/api/sessions/{processed}/feedback").text

    def test_detail_embeds_the_markdown_and_the_score(self, client, processed):
        detail = client.get(f"/api/sessions/{processed}").json()
        assert detail["status"] == "processed"
        assert detail["feedback_markdown"].startswith("# Feedback")
        assert detail["score"]["overall"] == pytest.approx(6.45, abs=0.01)

    def test_brief_annotates_sentences_with_measurements(self, client, processed):
        text = client.get(f"/api/sessions/{processed}/brief").text
        assert "S1." in text
        assert "hesitation" in text
        assert "USED" in text, "target-word status must reach the model"

    def test_transcript_json_is_served_for_the_player(self, client, processed):
        body = client.get(f"/api/sessions/{processed}/transcript").json()
        assert body["transcript"]["words"][0]["w"] == "So,"


class TestVocabularyAndProgress:
    def test_words_and_stats(self, client):
        client.post(
            "/api/sessions",
            json={"mode": "recommended", "topic": "t", "target_words": ["leverage", "de-risk"]},
        )
        assert len(client.get("/api/words").json()) == 2
        stats = client.get("/api/words/stats").json()
        assert stats["total"] == 2 and stats["due"] == 2
        assert len(client.get("/api/words/due?limit=1").json()) == 1

    def test_invalid_sort_is_rejected(self, client):
        assert client.get("/api/words?sort=vibes").status_code == 422

    def test_progress_is_empty_but_valid_with_no_scores(self, client):
        body = client.get("/api/progress").json()
        assert body["history"] == [] and body["latest"] is None
        assert sum(body["weights"].values()) == pytest.approx(1.0)


class TestSuggestions:
    def test_request_then_list(self, client):
        created = client.post(
            "/api/suggestions/requests", json={"mode": "interview", "category": "system-design"}
        )
        assert created.status_code == 201
        assert "/generate-topic" in created.json()["hint"]

    def test_suggestions_start_empty(self, client):
        assert client.get("/api/suggestions").json() == []


FAKE_TRANSCRIPT = {
    "mode": "recommended",
    "category": None,
    "topic": "Bottlenecks",
    "target_words": ["leverage"],
    "audio": {"file": "1.webm", "duration_sec": 4.5, "sample_rate": 16000},
    "transcript": {
        "text": "So, we had a bottleneck. I leveraged a queue.",
        "sentences": [
            {"i": 0, "text": "So, we had a bottleneck.", "start": 0.0, "end": 2.2,
             "words": 5, "wpm": 136.0},
            {"i": 1, "text": "I leveraged a queue.", "start": 3.0, "end": 4.3,
             "words": 4, "wpm": 184.0},
        ],
        "words": [{"w": "So,", "s": 0.0, "e": 0.3}, {"w": "leveraged", "s": 3.1, "e": 3.7}],
    },
    "speech": {
        "words_total": 9, "wpm_overall": 120.0, "wpm_speaking": 146.0, "speaking_sec": 3.7,
        "silence_sec": 0.8, "speech_ratio": 0.82, "sentence_count": 2, "avg_sentence_words": 4.5,
    },
    "pauses": {
        "count": 1, "per_minute": 13.3, "total_sec": 0.8, "longest_sec": 0.8,
        "mid_sentence_count": 0, "buckets": {"short_0.3_0.7": 0, "medium_0.7_1.5": 1,
                                             "long_1.5_plus": 0},
        "items": [{"start": 2.2, "dur": 0.8, "after_word": "bottleneck.", "sentence": 0,
                   "mid_sentence": False}],
    },
    "fillers": {
        "textual": {"total": 1, "hard_total": 0, "ambiguous_total": 1, "per_minute": 13.3,
                    "by_term": {"so": 1},
                    "items": [{"term": "so", "start": 0.0, "ambiguous": True, "word_index": 0}]},
        "acoustic": {"total": 1, "per_minute": 13.3, "note": "…",
                     "items": [{"start": 2.4, "dur": 0.5, "word_coverage": 0.0,
                                "after_word": "bottleneck.", "sentence": 0}]},
        "combined_total": 1, "combined_per_minute": 13.3,
    },
    "target_word_hits": [
        {"term": "leverage", "found": True, "count": 1,
         "occurrences": [{"start": 3.1, "said_as": "leveraged", "sentence": 1}]}
    ],
    "meta": {
        "pipeline_version": 1, "model": "large-v3", "compute_type": "int8_float16",
        "aligned": True, "language": "en", "vad": "silero", "elapsed_sec": 8.1,
        "generated_at": "2026-07-30T10:00:00+00:00", "thresholds": {},
    },
}
