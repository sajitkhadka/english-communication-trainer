"""Test fixtures.

Every test runs against a throwaway data directory, so nothing here can touch the real
`data/app.db` or the recordings in it. The settings object is cached with `lru_cache`,
so the redirect has to happen before anything imports the app modules.
"""

from __future__ import annotations

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
