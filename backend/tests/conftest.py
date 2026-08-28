"""Test fixtures.

Every test runs against a throwaway data directory, so nothing here can touch the real
`data/app.db` or the recordings in it. The settings object is cached with `lru_cache`,
so the redirect has to happen before anything imports the app modules.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from app.config import settings

    root = tmp_path / "data"
    docs = tmp_path / "docs"
    monkeypatch.setattr(settings, "data_dir", root)
    monkeypatch.setattr(settings, "docs_dir", docs)

    from app import db as dbmod

    dbmod.init_db()
    return root


@pytest.fixture
def conn(data_dir: Path):
    from app import db as dbmod

    connection = dbmod.connect()
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


@pytest.fixture
def client(data_dir: Path):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


# --------------------------------------------------------------------------- #
# the GPU pipeline, faked
# --------------------------------------------------------------------------- #


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
