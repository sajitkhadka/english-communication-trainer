"""The remote-capture half of ADR 0006: idempotent drains, the digest, the agent loop.

No relay, no server and no GPU are involved. The relay side is faked with
`httpx.MockTransport`, and the local API is the real FastAPI app driven through
`TestClient` - so a drain here exercises the same routes `ect agent` calls in
production, right down to the multipart upload.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
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
def relay_config(monkeypatch: pytest.MonkeyPatch):
    """The agent refuses to construct without both of these set."""
    from app.config import settings

    monkeypatch.setattr(settings, "relay_url", "http://relay.test")
    monkeypatch.setattr(settings, "relay_token", "token-abc")
    return settings


class FakeRelay:
    """A relay in a dict: an inbox, a digest slot, and a heartbeat record.

    Mounted behind `httpx.MockTransport`, so the agent's real HTTP code path runs.
    """

    def __init__(self, items: list[dict] | None = None, blobs: dict[str, bytes] | None = None):
        self.items = items or []
        self.blobs = blobs or {}
        self.acked: list[tuple[str, int]] = []
        self.failed: list[tuple[str, str]] = []
        self.digests: list[dict] = []
        self.heartbeats: list[dict] = []
        self.unauthorised: list[str] = []

    def client(self) -> httpx.Client:
        return httpx.Client(
            base_url="http://relay.test",
            headers={"authorization": "Bearer token-abc"},
            transport=httpx.MockTransport(self.handle),
        )

    def handle(self, request: httpx.Request) -> httpx.Response:  # noqa: PLR0911
        path = request.url.path
        if request.headers.get("authorization") != "Bearer token-abc":
            self.unauthorised.append(path)
            return httpx.Response(401, json={"detail": "agent token required"})

        if path == "/api/inbox/pending":
            pending = [i for i in self.items if i["uid"] not in {u for u, _ in self.acked}]
            return httpx.Response(200, json={"items": pending, "count": len(pending)})
        if path == "/api/agent/heartbeat":
            self.heartbeats.append(json.loads(request.content or b"{}"))
            return httpx.Response(200, json={"ok": True})
        if path == "/api/digest" and request.method == "PUT":
            self.digests.append(json.loads(request.content))
            return httpx.Response(200, json={"stored": True})
        if path.endswith("/blob"):
            uid = path.split("/")[3]
            if uid not in self.blobs:
                return httpx.Response(404, json={"detail": "no blob"})
            return httpx.Response(200, content=self.blobs[uid])
        if path.endswith("/ack"):
            uid = path.split("/")[3]
            self.acked.append((uid, json.loads(request.content)["session_id"]))
            return httpx.Response(200, json={"acked": True})
        if path.endswith("/fail"):
            uid = path.split("/")[3]
            self.failed.append((uid, json.loads(request.content)["error"]))
            return httpx.Response(200, json={"recorded": True})
        return httpx.Response(404, json={"detail": f"no route {path}"})  # pragma: no cover


# Headers that describe *this* hop rather than the message, so they must not be
# copied across the bridge - httpx recomputes them for the forwarded request.
_HOP_HEADERS = {"host", "content-length", "transfer-encoding", "connection"}


@pytest.fixture
def local_client(client):
    """The real API, reachable through a sync httpx.Client.

    `httpx.ASGITransport` is async-only and the agent is deliberately synchronous, so
    the bridge forwards through FastAPI's `TestClient`. The upshot is that a drain in
    these tests goes through the same routing, validation and multipart parsing that
    `ect agent` hits in production - not a stand-in for them.
    """

    def forward(request: httpx.Request) -> httpx.Response:
        response = client.request(
            request.method,
            request.url.path,
            params=request.url.params,
            content=request.content,
            headers={k: v for k, v in request.headers.items() if k.lower() not in _HOP_HEADERS},
        )
        return httpx.Response(
            response.status_code,
            content=response.content,
            headers={k: v for k, v in response.headers.items() if k.lower() not in _HOP_HEADERS},
        )

    return httpx.Client(base_url="http://local.test", transport=httpx.MockTransport(forward))


def make_agent(relay: FakeRelay, local_client: httpx.Client):
    from app.agent import Agent

    return Agent(relay=relay.client(), local=local_client)


def item(uid: str, mode: str = "freeform", **extra) -> dict:
    return {
        "uid": uid,
        "mode": mode,
        "filename": f"{uid}.webm",
        "content_type": "audio/webm",
        "bytes": 9,
        "attempts": 0,
        **extra,
    }


# --------------------------------------------------------------------------- #
# external_uid: the property that makes a retried drain safe
# --------------------------------------------------------------------------- #


class TestExternalUid:
    def test_same_uid_returns_the_same_session(self, services):
        first = services.create_session(mode="freeform", topic="a", external_uid="cap-1")
        second = services.create_session(mode="freeform", topic="b", external_uid="cap-1")
        assert second["id"] == first["id"]
        # The second call must not have rewritten the row either - a re-drain is a
        # no-op, not an update with whatever the relay happened to send this time.
        assert second["topic"] == "a"

    def test_a_redrain_does_not_reset_a_processed_session(self, services, dbmod):
        session = services.create_session(mode="journal", topic="t", external_uid="cap-2")
        with dbmod.cursor() as conn:
            dbmod.update_session(conn, session["id"], status="processed")

        again = services.create_session(mode="journal", topic="t", external_uid="cap-2")
        assert again["status"] == "processed"

    def test_sessions_without_a_uid_are_unaffected(self, services, dbmod):
        a = services.create_session(mode="freeform", topic="a")
        b = services.create_session(mode="freeform", topic="b")
        assert a["id"] != b["id"]
        assert a["external_uid"] is None

    def test_the_unique_index_exists_on_a_migrated_database(self, dbmod):
        # `schema.sql` is CREATE TABLE IF NOT EXISTS, not a migration system, so the
        # column arrives by ALTER TABLE on an existing DB. Both paths have to end up
        # with the uniqueness that makes a re-drain a no-op.
        with dbmod.cursor() as conn:
            indexes = {r["name"] for r in conn.execute("PRAGMA index_list(sessions)")}
        assert "idx_sessions_external_uid" in indexes

    def test_api_accepts_and_returns_external_uid(self, client):
        created = client.post(
            "/api/sessions", json={"mode": "worklog", "external_uid": "cap-3"}
        ).json()
        again = client.post(
            "/api/sessions", json={"mode": "worklog", "external_uid": "cap-3"}
        ).json()
        assert again["id"] == created["id"]
        assert again["external_uid"] == "cap-3"


# --------------------------------------------------------------------------- #
# the digest
# --------------------------------------------------------------------------- #


class TestDigest:
    def test_version_is_stable_across_rebuilds(self, data_dir, services):
        from app.digest import build_digest

        services.create_session(mode="freeform", topic="one")
        first, second = build_digest(), build_digest()
        assert first["version"] == second["version"]
        # ... and the timestamps differ, which is exactly why they are excluded from
        # the hash: otherwise every poll would push an identical snapshot.
        assert "generated_at" in first

    def test_version_moves_when_the_data_moves(self, data_dir, services):
        from app.digest import build_digest

        before = build_digest()["version"]
        services.create_session(mode="freeform", topic="new session")
        assert build_digest()["version"] != before

    def test_feedback_horizon_limits_the_markdown(self, data_dir, services, dbmod):
        from app.digest import build_digest
        from app.paths import feedback_path

        ids = []
        for n in range(4):
            session = services.create_session(mode="freeform", topic=f"s{n}")
            feedback_path(session["id"]).write_text(f"# feedback {n}", encoding="utf-8")
            ids.append(session["id"])

        digest = build_digest(feedback_horizon=2)
        details = digest["session_details"]
        # Newest-first, so the horizon keeps the two most recent.
        assert set(details) == {str(ids[-1]), str(ids[-2])}
        assert details[str(ids[-1])]["feedback_markdown"] == "# feedback 3"
        # The older sessions are still listed - metadata survives, the prose does not.
        assert len(digest["sessions"]) == 4

    def test_carries_every_route_the_relay_serves_offline(self, data_dir, services):
        from app.digest import build_digest

        digest = build_digest()
        for key in (
            "health",
            "sessions",
            "session_details",
            "notes",
            "words",
            "words_due",
            "word_stats",
            "suggestions",
            "queue",
            "progress",
        ):
            assert key in digest, f"the relay serves /{key} offline but the digest omits it"

    def test_sessions_carry_the_same_flags_the_api_returns(self, client, services):
        """The relay serves these rows verbatim, so a flag computed differently here
        would show up as the UI disagreeing with itself depending on who answered."""
        from app.digest import build_digest

        services.create_session(mode="freeform", topic="t")
        from_api = client.get("/api/sessions").json()[0]
        from_digest = build_digest()["sessions"][0]
        for key in ("id", "mode", "status", "has_audio", "has_transcript", "has_feedback"):
            assert from_api[key] == from_digest[key]

    def test_exposed_over_http(self, client):
        payload = client.get("/api/digest").json()
        assert payload["version"]
        assert payload["schema_version"] == 1

    def test_horizon_is_overridable_over_http(self, client):
        assert client.get("/api/digest?horizon=1").json()["feedback_horizon"] == 1


# --------------------------------------------------------------------------- #
# the agent
# --------------------------------------------------------------------------- #


class TestAgentConfiguration:
    def test_refuses_to_run_without_a_relay_url(self, data_dir, monkeypatch):
        from app.agent import Agent, AgentError
        from app.config import settings

        monkeypatch.setattr(settings, "relay_url", "")
        monkeypatch.setattr(settings, "relay_token", "t")
        with pytest.raises(AgentError, match="ECT_RELAY_URL"):
            Agent()

    def test_refuses_to_run_without_a_token(self, data_dir, monkeypatch):
        from app.agent import Agent, AgentError
        from app.config import settings

        monkeypatch.setattr(settings, "relay_url", "http://relay.test")
        monkeypatch.setattr(settings, "relay_token", "")
        with pytest.raises(AgentError, match="ECT_RELAY_TOKEN"):
            Agent()

    def test_status_reports_which_half_is_unreachable(self, data_dir, monkeypatch):
        from app.agent import status
        from app.config import settings

        monkeypatch.setattr(settings, "relay_url", "")
        report = status()
        assert report["ok"] is False
        assert report["relay"] == "not configured"


class TestDrain:
    def test_drains_a_capture_into_a_local_session(
        self, relay_config, local_client, client, fake_transcribe
    ):
        relay = FakeRelay(
            items=[item("cap-10", mode="worklog", topic="Tuesday")],
            blobs={"cap-10": b"fake-audio"},
        )
        with make_agent(relay, local_client) as agent:
            result = agent.drain()

        assert result.as_dict()["counts"] == {"drained": 1, "skipped": 0, "failed": 0}
        drained = result.drained[0]
        assert drained["mode"] == "worklog"

        session = client.get(f"/api/sessions/{drained['session_id']}").json()
        assert session["external_uid"] == "cap-10"
        assert session["topic"] == "Tuesday"
        assert session["has_audio"] is True
        # ADR 0003 stands: the recording arrives, but the Claude handoff is still
        # pulled by the user. The agent must never enqueue.
        assert session["status"] == "recorded"

    def test_acks_only_after_the_audio_is_on_disk(
        self, relay_config, local_client, client, fake_transcribe
    ):
        relay = FakeRelay(items=[item("cap-11")], blobs={"cap-11": b"audio"})
        with make_agent(relay, local_client) as agent:
            agent.drain()

        assert len(relay.acked) == 1
        uid, session_id = relay.acked[0]
        assert uid == "cap-11"
        assert client.get(f"/api/sessions/{session_id}").json()["has_audio"] is True

    def test_a_lost_ack_costs_one_repeated_pass_not_a_duplicate(
        self, relay_config, local_client, client, fake_transcribe
    ):
        relay = FakeRelay(items=[item("cap-12")], blobs={"cap-12": b"audio"})
        with make_agent(relay, local_client) as agent:
            first = agent.drain()
            relay.acked.clear()  # the ack never reached the relay
            second = agent.drain()

        assert first.drained[0]["session_id"] == second.drained[0]["session_id"]
        assert len(client.get("/api/sessions").json()) == 1

    def test_a_failing_capture_is_reported_and_does_not_stop_the_others(
        self, relay_config, local_client, client, fake_transcribe
    ):
        relay = FakeRelay(
            items=[item("cap-13"), item("cap-14")],
            blobs={"cap-14": b"audio"},  # cap-13's blob is missing
        )
        with make_agent(relay, local_client) as agent:
            result = agent.drain()

        assert [f["uid"] for f in result.failed] == ["cap-13"]
        assert [d["uid"] for d in result.drained] == ["cap-14"]
        assert relay.failed and relay.failed[0][0] == "cap-13"

    def test_skips_a_capture_past_the_attempt_limit(self, relay_config, local_client):
        from app.agent import MAX_ATTEMPTS

        relay = FakeRelay(items=[item("cap-15", attempts=MAX_ATTEMPTS)])
        with make_agent(relay, local_client) as agent:
            result = agent.drain()

        assert [s["uid"] for s in result.skipped] == ["cap-15"]
        assert not relay.acked

    def test_an_unreachable_relay_is_not_an_exception(self, relay_config, local_client):
        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("relay down")

        from app.agent import Agent

        relay_client = httpx.Client(
            base_url="http://relay.test", transport=httpx.MockTransport(refuse)
        )
        with Agent(relay=relay_client, local=local_client) as agent:
            assert agent.drain().as_dict()["counts"]["drained"] == 0

    def test_a_bad_mode_fails_that_capture_only(self, relay_config, local_client):
        relay = FakeRelay(items=[item("cap-16", mode="nonsense")], blobs={"cap-16": b"a"})
        with make_agent(relay, local_client) as agent:
            result = agent.drain()

        assert len(result.failed) == 1
        assert not relay.acked


class TestHeartbeat:
    def test_reports_whether_the_local_api_answered(self, relay_config, local_client):
        relay = FakeRelay()
        with make_agent(relay, local_client) as agent:
            agent.heartbeat()
        assert relay.heartbeats == [{"api_ok": True}]

    def test_a_dead_local_api_heartbeats_api_ok_false(self, relay_config):
        """The relay must not treat "the agent is alive" as "requests get answered",
        or every call pays a proxy timeout to discover otherwise."""

        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("uvicorn is down")

        from app.agent import Agent

        relay = FakeRelay()
        dead_local = httpx.Client(
            base_url="http://local.test", transport=httpx.MockTransport(refuse)
        )
        with Agent(relay=relay.client(), local=dead_local) as agent:
            agent.heartbeat()
        assert relay.heartbeats == [{"api_ok": False}]


class TestDigestPush:
    def test_pushes_once_then_stops_while_nothing_changes(self, relay_config, local_client):
        relay = FakeRelay()
        with make_agent(relay, local_client) as agent:
            first = agent.push_digest()
            second = agent.push_digest()

        assert first["pushed"] is True
        assert second == {"pushed": False, "version": first["version"], "reason": "unchanged"}
        assert len(relay.digests) == 1

    def test_pushes_again_when_the_data_changes(self, relay_config, local_client, services):
        relay = FakeRelay()
        with make_agent(relay, local_client) as agent:
            agent.push_digest()
            services.create_session(mode="freeform", topic="something new")
            assert agent.push_digest()["pushed"] is True

        assert len(relay.digests) == 2
        assert relay.digests[0]["version"] != relay.digests[1]["version"]

    def test_force_pushes_an_unchanged_snapshot(self, relay_config, local_client):
        relay = FakeRelay()
        with make_agent(relay, local_client) as agent:
            agent.push_digest()
            assert agent.push_digest(force=True)["pushed"] is True
        assert len(relay.digests) == 2

    def test_the_pushed_snapshot_is_what_the_relay_serves(
        self, relay_config, local_client, services
    ):
        services.create_session(mode="brainstorm", topic="ideas")
        relay = FakeRelay()
        with make_agent(relay, local_client) as agent:
            agent.push_digest()

        pushed = relay.digests[0]
        assert [s["topic"] for s in pushed["sessions"]] == ["ideas"]
        assert pushed["notes"]["markdown"]


class TestJournalStaysOutOfTheQueue:
    def test_a_drained_journal_is_never_enqueued(
        self, relay_config, local_client, client, fake_transcribe
    ):
        """`journal` finalises itself at transcription and must never reach Claude -
        a backend invariant, so arriving over the network cannot bypass it either."""
        relay = FakeRelay(items=[item("cap-20", mode="journal")], blobs={"cap-20": b"audio"})
        with make_agent(relay, local_client) as agent:
            result = agent.drain()

        session_id = result.drained[0]["session_id"]
        assert client.post(f"/api/sessions/{session_id}/process").status_code == 409
        assert client.get("/api/queue").json()["count"] == 0
