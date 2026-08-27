"""`ect agent` - the PC half of ADR 0006. Drains the relay inbox, pushes the digest.

Three jobs, one loop:

1. **Drain.** Recordings captured on a phone land in the relay's inbox as blobs. The
   agent pulls them down, turns each into a local session, stores the audio and
   transcribes it, then acks - at which point the relay deletes the blob, because a
   `worklog` recording is full of employer and project detail and the server is
   publicly addressable.
2. **Heartbeat.** Tells the relay the PC is up, so the relay knows whether to proxy a
   request or answer it from the digest. A stale heartbeat plus queued inbox work is
   also what makes the relay send a Wake-on-LAN packet.
3. **Digest push.** Fetches `GET /api/digest` from the local API, and forwards it only
   when its `version` differs from the last one accepted.

**It talks to the local HTTP API and never imports `services`.** That is a correctness
requirement, not a layering preference: `services._gpu_lock` is a `threading.Lock`, so
it only guards one process. Driving transcription through `POST /transcribe` keeps
every WhisperX load inside the single uvicorn process where that lock means something.
Two processes each loading `large-v3` do not fit in 6 GB and take the worker down
natively - no Python exception, so `transcribe_status` would sit at `running` forever
with nothing to clear it. As a bonus the agent needs no torch, no CUDA, and no
`pipeline` import at all.

**Drained recordings stop at `recorded`.** ADR 0003 made the Claude handoff
user-pulled, and arriving over the network does not change that. The agent never calls
`/process`. The one exception is free: `journal` finalises itself the moment
transcription finishes, inside the backend, so a journal recorded on the commute is
already complete.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import settings

log = logging.getLogger("ect.agent")

# A drain that fails this many times in a row is reported in the item's status and
# skipped for the rest of the pass, so one poisonous blob cannot starve the others.
MAX_ATTEMPTS = 5


class AgentError(RuntimeError):
    """The agent cannot run at all - misconfiguration, not a transient failure."""


@dataclass
class DrainResult:
    """What one pass over the inbox did. The shape `ect agent once` prints."""

    drained: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "drained": self.drained,
            "skipped": self.skipped,
            "failed": self.failed,
            "counts": {
                "drained": len(self.drained),
                "skipped": len(self.skipped),
                "failed": len(self.failed),
            },
        }


def _require_config() -> None:
    if not settings.relay_url:
        raise AgentError(
            "ECT_RELAY_URL is not set - the agent has no inbox to drain. "
            "See docs/relay.md; leave it unset to run the app desk-only."
        )
    if not settings.relay_token:
        raise AgentError(
            "ECT_RELAY_TOKEN is not set - the relay rejects unauthenticated agents. "
            "It must match the token in the relay's Secret."
        )


class Agent:
    """The drain/heartbeat/digest loop.

    Both clients are injectable so the tests can drive a full pass through
    `httpx.MockTransport` without a relay, a server, or a GPU.
    """

    def __init__(
        self,
        *,
        relay: httpx.Client | None = None,
        local: httpx.Client | None = None,
    ) -> None:
        _require_config()
        self.relay = relay or httpx.Client(
            base_url=settings.relay_url.rstrip("/"),
            headers={"authorization": f"Bearer {settings.relay_token}"},
            timeout=settings.agent_http_timeout_sec,
        )
        self.local = local or httpx.Client(
            base_url=settings.local_api_url.rstrip("/"),
            timeout=settings.agent_http_timeout_sec,
        )
        # The last digest version the relay confirmed. Held in memory only: after a
        # restart the first push is redundant rather than wrong, which is the cheaper
        # of the two failure modes.
        self._digest_version: str | None = None

    # ----------------------------------------------------------------- lifecycle
    def close(self) -> None:
        self.relay.close()
        self.local.close()

    def __enter__(self) -> Agent:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---------------------------------------------------------------- heartbeat
    def heartbeat(self) -> dict[str, Any]:
        """Tell the relay the PC is up. Includes whether the API is actually serving.

        `pc_online` in the relay means "requests will be answered", not "the machine
        has power", so an agent running against a dead uvicorn must not claim online.
        """
        api_ok = False
        try:
            api_ok = self.local.get("/api/health").status_code == 200
        except httpx.HTTPError as exc:
            log.debug("local API not answering: %s", exc)
        response = self.relay.post("/api/agent/heartbeat", json={"api_ok": api_ok})
        response.raise_for_status()
        return response.json()

    # -------------------------------------------------------------------- drain
    def pending(self) -> list[dict[str, Any]]:
        response = self.relay.get("/api/inbox/pending")
        response.raise_for_status()
        return response.json().get("items", [])

    def drain(self) -> DrainResult:
        """One pass over the inbox. Never raises for a single bad item."""
        result = DrainResult()
        try:
            items = self.pending()
        except httpx.HTTPError as exc:
            log.warning("could not list the relay inbox: %s", exc)
            return result

        for item in items:
            uid = item.get("uid")
            if not uid:  # pragma: no cover - the relay always sets it
                continue
            if int(item.get("attempts", 0)) >= MAX_ATTEMPTS:
                result.skipped.append({"uid": uid, "reason": "too many failed attempts"})
                continue
            try:
                result.drained.append(self.drain_one(item))
            except Exception as exc:
                log.exception("drain failed for %s", uid)
                result.failed.append({"uid": uid, "error": str(exc)})
                self._report_failure(uid, exc)
        return result

    def drain_one(self, item: dict[str, Any]) -> dict[str, Any]:
        """Inbox item -> local session, audio stored, transcribed, acked.

        Every step is safe to repeat. `external_uid` makes session creation idempotent,
        re-storing the same audio is a plain overwrite, and transcription is a no-op
        once a transcript exists - so an ack lost on the wire costs one repeated pass,
        never a duplicate session.
        """
        uid = item["uid"]
        session = self._create_session(item)
        session_id = session["id"]

        if session.get("status") == "awaiting_recording" or not session.get("has_audio"):
            blob = self.relay.get(f"/api/inbox/{uid}/blob")
            blob.raise_for_status()
            filename = item.get("filename") or f"{uid}.webm"
            content_type = item.get("content_type") or "application/octet-stream"
            upload = self.local.post(
                f"/api/sessions/{session_id}/recording",
                files={"file": (filename, blob.content, content_type)},
            )
            upload.raise_for_status()
            session = upload.json()

        transcription: dict[str, Any] | None = None
        if not session.get("has_transcript"):
            response = self.local.post(
                f"/api/sessions/{session_id}/transcribe",
                timeout=settings.agent_transcribe_timeout_sec,
            )
            response.raise_for_status()
            transcription = response.json()

        # Ack last, and only after the audio is on the PC's disk: the relay deletes the
        # blob on ack, so acking any earlier would trade a duplicate for a lost
        # recording. The blob is the only copy until this point.
        ack = self.relay.post(f"/api/inbox/{uid}/ack", json={"session_id": session_id})
        ack.raise_for_status()

        log.info("drained %s -> session %s (%s)", uid, session_id, session.get("mode"))
        return {
            "uid": uid,
            "session_id": session_id,
            "mode": session.get("mode"),
            "status": session.get("status"),
            "transcription": transcription,
        }

    def _create_session(self, item: dict[str, Any]) -> dict[str, Any]:
        body = {
            "mode": item.get("mode") or "freeform",
            "topic": item.get("topic"),
            "notes": item.get("notes"),
            "external_uid": item["uid"],
        }
        response = self.local.post("/api/sessions", json=body)
        response.raise_for_status()
        return response.json()

    def _report_failure(self, uid: str, exc: Exception) -> None:
        """Record the failure on the relay so the item's attempt count advances.

        Without this a permanently undrainable blob is retried every poll forever, and
        the reason is only ever visible in the PC's log - the one place you cannot
        reach from the phone that recorded it.
        """
        try:
            self.relay.post(f"/api/inbox/{uid}/fail", json={"error": str(exc)[:500]})
        except httpx.HTTPError:
            log.debug("could not report the failure of %s to the relay", uid)

    # ------------------------------------------------------------------- digest
    def push_digest(self, *, force: bool = False) -> dict[str, Any]:
        """Mirror the local snapshot to the relay, but only when it changed."""
        response = self.local.get("/api/digest")
        response.raise_for_status()
        payload = response.json()
        version = payload.get("version")
        if version == self._digest_version and not force:
            return {"pushed": False, "version": version, "reason": "unchanged"}
        push = self.relay.put("/api/digest", json=payload)
        push.raise_for_status()
        self._digest_version = version
        log.info("digest pushed (version %s, %s sessions)", version, len(payload["sessions"]))
        return {"pushed": True, "version": version, "sessions": len(payload["sessions"])}

    # --------------------------------------------------------------------- loop
    def run_forever(self) -> None:  # pragma: no cover - exercised by hand
        """Poll until interrupted. Every iteration is wrapped: the loop outlives a
        relay restart, a `dev.ps1` restart, and the PC's network coming up late."""
        log.info(
            "agent up: relay=%s local=%s poll=%.0fs digest=%.0fs",
            settings.relay_url,
            settings.local_api_url,
            settings.agent_poll_sec,
            settings.agent_digest_sec,
        )
        next_digest = 0.0
        while True:
            started = time.monotonic()
            try:
                self.heartbeat()
                self.drain()
            except httpx.HTTPError as exc:
                log.warning("poll failed (relay unreachable?): %s", exc)
            except Exception:
                log.exception("unexpected error during poll")

            if started >= next_digest:
                try:
                    self.push_digest()
                except httpx.HTTPError as exc:
                    log.warning("digest push failed: %s", exc)
                except Exception:
                    log.exception("unexpected error building the digest")
                next_digest = started + settings.agent_digest_sec

            elapsed = time.monotonic() - started
            time.sleep(max(1.0, settings.agent_poll_sec - elapsed))


def status() -> dict[str, Any]:
    """Can the agent reach both ends? What `ect agent status` prints.

    Deliberately reports rather than raises: the whole point is to say *which* half is
    unreachable, which is the answer whenever remote capture stops working.
    """
    report: dict[str, Any] = {
        "relay_url": settings.relay_url or None,
        "relay_token_set": bool(settings.relay_token),
        "local_api_url": settings.local_api_url,
    }
    if not settings.relay_url:
        report["relay"] = "not configured"
        report["ok"] = False
        return report

    with httpx.Client(timeout=10.0) as client:
        try:
            response = client.get(f"{settings.local_api_url.rstrip('/')}/api/health")
            report["local_api"] = "ok" if response.status_code == 200 else response.status_code
        except httpx.HTTPError as exc:
            report["local_api"] = f"unreachable: {exc}"
        try:
            response = client.get(
                f"{settings.relay_url.rstrip('/')}/api/relay/status",
                headers={"authorization": f"Bearer {settings.relay_token}"},
            )
            report["relay"] = (
                response.json() if response.status_code == 200 else response.status_code
            )
        except httpx.HTTPError as exc:
            report["relay"] = f"unreachable: {exc}"

    report["ok"] = report.get("local_api") == "ok" and isinstance(report.get("relay"), dict)
    return report
