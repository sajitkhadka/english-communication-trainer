"""The worklog capture path: one write path, journal outlives session (PRD-worklog)."""

from __future__ import annotations

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


@pytest.fixture
def worklog_session(services):
    session = services.create_session(mode="worklog", topic=None)
    services.store_recording(session["id"], b"audio", suffix=".webm")
    return session


ENTRY_MD = "# 2026-08-14\n\n## Did\n- shipped batched writes\n"


def add_entry(services, *, date="2026-08-14", session_id=None, **overrides):
    kwargs = {
        "entry_date": date,
        "markdown": ENTRY_MD,
        "summary": "Shipped batched writes; cut p99 40%",
        "projects": ["payment-migration"],
        "tags": ["debugging", "cross-team"],
        "session_id": session_id,
    }
    kwargs.update(overrides)
    return services.record_worklog_entry(**kwargs)


class TestWorklogSessions:
    def test_worklog_needs_no_topic_and_gets_a_dated_default(self, services, dbmod):
        session = services.create_session(mode="worklog", topic=None)
        assert session["mode"] == "worklog"
        assert session["topic"] == f"Worklog - {dbmod.today()}"

    def test_enqueue_points_at_log_work_not_process_session(
        self, services, worklog_session, monkeypatch
    ):
        from app.config import settings

        monkeypatch.setattr(settings, "transcribe_on_upload", False)
        result = services.enqueue(worklog_session["id"])
        assert result["status"] == "pending"
        assert "/log-work" in result["hint"]

    def test_feedback_apply_refuses_worklog_sessions(self, services, worklog_session):
        with pytest.raises(services.WorkflowError, match="worklog add"):
            services.record_feedback(
                {"session_id": worklog_session["id"], "scores": {"fluency": 5.0}}
            )


class TestRecordEntry:
    def test_files_markdown_indexes_and_completes_the_session(
        self, services, dbmod, worklog_session, data_dir
    ):
        from app.paths import queue_marker, worklog_daily_path

        sid = worklog_session["id"]
        marker = queue_marker(sid)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("queued", encoding="utf-8")

        result = add_entry(services, session_id=sid)

        stored = worklog_daily_path("2026-08-14")
        assert stored.read_text(encoding="utf-8") == ENTRY_MD
        assert stored == data_dir / "worklog" / "daily" / "2026-08-14.md"
        assert not marker.exists()

        with dbmod.cursor() as conn:
            entry = dbmod.get_worklog_entry(conn, "2026-08-14")
            session = dbmod.get_session(conn, sid)
        assert entry["projects"] == ["payment-migration"]
        assert entry["tags"] == ["debugging", "cross-team"]
        assert entry["session_id"] == sid
        assert session["status"] == "processed"
        # The frontend renders the journal entry where feedback would go.
        assert session["feedback_path"] == result["path"]
        assert session["feedback_path"].endswith("worklog/daily/2026-08-14.md")

    def test_entry_without_a_session_is_fine(self, services, dbmod):
        result = add_entry(services)  # typed directly, no recording behind it
        assert result["session_id"] is None
        with dbmod.cursor() as conn:
            assert dbmod.get_worklog_entry(conn, "2026-08-14") is not None

    def test_same_day_add_overwrites_one_row(self, services, dbmod):
        from app.paths import worklog_daily_path

        add_entry(services)
        add_entry(services, markdown="# merged\n", summary="Merged entry", tags=["delivery"])

        assert worklog_daily_path("2026-08-14").read_text(encoding="utf-8") == "# merged\n"
        with dbmod.cursor() as conn:
            rows = dbmod.list_worklog_entries(conn)
        assert len(rows) == 1
        assert rows[0]["summary"] == "Merged entry"
        assert rows[0]["tags"] == ["delivery"]

    def test_unknown_tags_are_rejected(self, services):
        with pytest.raises(services.WorkflowError, match="unknown worklog tags"):
            add_entry(services, tags=["synergy"])

    def test_bad_dates_are_rejected(self, services):
        with pytest.raises(services.WorkflowError, match="ISO date"):
            add_entry(services, date="Aug 14")

    def test_summary_is_required(self, services):
        with pytest.raises(services.WorkflowError, match="summary"):
            add_entry(services, summary="  ")

    def test_non_worklog_sessions_cannot_claim_an_entry(self, services):
        practice = services.create_session(mode="freeform", topic="t")
        with pytest.raises(services.WorkflowError, match="not worklog"):
            add_entry(services, session_id=practice["id"])

    def test_the_entry_outlives_its_session(self, services, dbmod, worklog_session):
        """The journal is the archive; deleting the capture must not touch it."""
        sid = worklog_session["id"]
        add_entry(services, session_id=sid)
        with dbmod.cursor() as conn:
            conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        with dbmod.cursor() as conn:
            entry = dbmod.get_worklog_entry(conn, "2026-08-14")
        assert entry is not None
        assert entry["session_id"] is None


class TestListing:
    def test_filters_by_month_tag_and_project(self, services, dbmod):
        add_entry(services, date="2026-07-30", projects=["oncall"], tags=["failure"])
        add_entry(services, date="2026-08-14")

        def dates(**filters):
            with dbmod.cursor() as conn:
                return [r["entry_date"] for r in dbmod.list_worklog_entries(conn, **filters)]

        assert dates(month="2026-07") == ["2026-07-30"]
        assert dates(tag="debugging") == ["2026-08-14"]
        assert dates(project="oncall") == ["2026-07-30"]
        assert dates(tag="leadership") == []


class TestRollups:
    def test_status_flags_completed_months_without_a_rollup(self, services, dbmod):
        add_entry(services, date="2026-01-05")
        add_entry(services, date=dbmod.today())  # current month never counts as missing

        status = services.worklog_rollup_status()
        assert status["missing"] == ["2026-01"]

        services.record_worklog_rollup(month="2026-01", markdown="# January\n")
        status = services.worklog_rollup_status()
        assert status["missing"] == []
        assert status["rollups"] == ["2026-01"]

        from app.paths import worklog_rollup_path

        assert worklog_rollup_path("2026-01").read_text(encoding="utf-8") == "# January\n"

    def test_bad_month_is_rejected(self, services):
        with pytest.raises(services.WorkflowError, match="YYYY-MM"):
            services.record_worklog_rollup(month="January", markdown="x")


class TestApi:
    def test_session_detail_renders_the_journal_entry(self, client, services, worklog_session):
        add_entry(services, session_id=worklog_session["id"])
        detail = client.get(f"/api/sessions/{worklog_session['id']}").json()
        assert detail["has_feedback"] is True
        assert detail["feedback_markdown"] == ENTRY_MD

    def test_worklog_mode_is_accepted_over_http(self, client):
        created = client.post("/api/sessions", json={"mode": "worklog"})
        assert created.status_code == 201
        assert created.json()["mode"] == "worklog"
