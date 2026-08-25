# ADR 0006 — Remote capture, local processing

**Status:** Proposed · **Date:** 2026-08-25 · **Extends:** ADR 0003

## Context

Everything currently lives on one Windows PC: FastAPI on `127.0.0.1:8000`, SQLite at
`data/app.db`, the WhisperX/Silero pipeline on a 6 GB CUDA GPU, and the Claude Code
skills that read `data/profile.md` off the local disk. That is the right shape for the
analysis and the wrong shape for the microphone.

The two halves have nothing in common operationally. **Capture** is a mic and a
`MediaRecorder` — it needs to be wherever the user is, and it needs no GPU, no database
and no model. **Processing** needs 6 GB of VRAM and a Claude Code console, which is one
machine that is frequently asleep. Binding them together means the tool only exists at
the desk, and the sessions most worth having are the ones it cannot take: the worklog on
the commute, the brainstorm on a walk, the journal entry away from home.

The available infrastructure shapes what is worth building. A home Linux server sits on
the same LAN as the PC, is publicly reachable through Nginx Proxy Manager on 80/443 with
Let's Encrypt, runs Docker with a private registry on `:5000`, and terminates WireGuard
on `:64648`.

Two facts constrain the solution more than they first appear:

- `getUserMedia` requires a secure context. Any remotely reachable recorder must be
  HTTPS, not merely reachable.
- `services._gpu_lock` is a `threading.Lock`, so it is process-wide only. Two processes
  each loading `large-v3` do not fit in 6 GB and take the worker down natively, with no
  Python exception, leaving `transcribe_status` stuck at `running` forever.

## Decision

**Split on capture vs. processing, and give the server an inbox rather than a copy of
the app.**

| Piece | Runs on | Owns |
| --- | --- | --- |
| `relay/` | home server, Docker | recorder PWA, inbox blobs + `inbox.db`, digest snapshot, the `/api` switchboard |
| `ect agent` | PC, at boot | drain loop, heartbeat, digest push |
| backend + frontend | PC | everything else, unchanged |

Four sub-decisions carry the weight.

**1. The relay is a switchboard on the existing `/api` paths, not a second app.**
`frontend/src/api.ts` uses `const BASE = "/api"` — a relative base — so the Vite build
served by the relay is the same app talking to the same routes. The relay decides where
they land:

| PC state | `GET` sessions / feedback / notes | writes, `/transcribe`, `/process` |
| --- | --- | --- |
| online | proxied to the PC over the LAN | proxied to the PC |
| offline | answered from the digest snapshot | `503` with `{"detail": "PC offline"}` |

`api.ts` already surfaces `detail` on failure, so an offline write degrades into a
readable message with no frontend change. One SQLite file remains the source of truth,
and the seams in `CLAUDE.md` do not move: `ect` still opens the local database, skills
still read `data/profile.md` from local disk, `services.py` is still the only write path.

**2. The agent talks to the local HTTP API and never imports `services`.** It creates
sessions and triggers transcription through `POST /api/sessions`, `/recording` and
`/transcribe`, so every WhisperX load happens inside the one server process and the
existing lock still holds. This is a correctness requirement, not a layering preference —
see the VRAM note in Context. It also means the agent needs no torch, no CUDA and no
pipeline import at all.

**3. The digest is one-way and derived.** The agent polls the local API on a timer,
hashes the payload, and `POST`s to the relay only when it changes — so it captures
`/process-session` runs, frontend edits and CLI writes identically without hooking any of
them. The relay serves it and never writes into it. That is what keeps
`services.write_notes`'s version/409 contract a local concern instead of a distributed
one. The snapshot carries full feedback markdown for the most recent ~50 sessions and
metadata only beyond that.

**4. Drained recordings stop at `recorded`.** ADR 0003 made the Claude handoff
user-pulled, and arriving over the network does not change that: the agent creates the
session, stores the audio and transcribes, then stops. The user still presses "Ready for
AI processing" and runs `/process-session`. `journal` is the free exception —
`_transcribe_session` finalises it to `processed` directly, so a journal recorded on the
commute is complete before the user sits down.

**Transport.** The server is on the same LAN, so the PC gets a DHCP reservation and
`settings.host` moves from `127.0.0.1` to that specific LAN address. No tunnel is
involved in the data path. Wake-on-LAN covers a sleeping PC: queued work plus a stale
heartbeat triggers a magic packet.

Supporting changes: an `ADDITIVE` `external_uid TEXT UNIQUE` column on `sessions` for
idempotent drains (`schema.sql` is `CREATE TABLE IF NOT EXISTS`, not a migration system,
so this is an explicit `ALTER TABLE`); an inbound firewall rule on 8000 scoped to the
server's address; NPM access control in front of the recorder.

## Alternatives

**Move the whole app to the server; make the PC a GPU worker that leases jobs.** The
cleaner distributed-systems answer, and the one that makes the phone fully independent.
It also relocates the database away from `ect` and the skills. `CLAUDE.md` seam #1 is
that the CLI is the only interface skills use and that it works with the server stopped;
a remote database either breaks that or forces `ect` to become an HTTP client, which
trades a guarantee for a network dependency in the one path that has to keep working.
Rejected for the blast radius, not the idea.

**Bidirectional SQLite sync (Litestream, rqlite, or a CRDT layer).** Would give genuine
offline read *and* write. It also introduces a second writer to a schema with an explicit
optimistic-concurrency contract on notes and a workflow state machine on sessions, and
merge conflicts in that state machine have no obviously correct resolution. The digest
buys most of the benefit for a fraction of the surface, precisely because it refuses to
be writable.

**WireGuard only, no server-side piece.** Adding a phone peer to the existing tunnel
makes the app reachable from anywhere today, with zero code. It was rejected only because
it does not survive the PC being off — which is the actual requirement. Worth recording
that if the goal were remote access alone, nothing in this ADR would need to exist.

**A cloud transcription API for recordings made while the PC is off.** Would close the
loop without the PC. PRD Non-Goals §2 rules out the cloud API, and the deterministic
metric layer is calibrated against this specific WhisperX + wav2vec2 + Silero stack —
scores from a different transcriber would not be comparable across sessions, which is the
property the whole scoring design exists to preserve.

**A reverse tunnel (Cloudflare Tunnel, `ssh -R`, frp) instead of a LAN address.** The
right answer if the server were hosted elsewhere, and the fallback if the PC ever moves
off this network. On a shared LAN it adds a hop, a daemon and a dependency to reach a
machine already reachable.

**A separate repository for the relay.** It deploys to a different host and has no shared
dependency with the backend. But it is the same product, its contract is defined entirely
by this app's routes, and a drifting inbox schema across two repos is a worse problem than
a folder that Docker builds independently. It stays in-tree as `relay/`, built to the
private registry.

## Consequences

- Capture stops depending on the desk. Anything without a topic — `freeform`, `worklog`,
  `brainstorm`, `journal` — can be recorded from a phone with no prior setup, and
  `journal` completes end-to-end without the user touching the PC.
- The PC is still required for everything except capture and reading history. This is a
  deliberate ceiling: the system degrades to "record and browse" rather than to nothing,
  and never to "silently processed by something else."
- The API leaves the loopback interface. A LAN-scoped firewall rule and a DHCP
  reservation are load-bearing, not hygiene — `PUT /api/notes` and
  `DELETE /api/sessions/{id}` are unauthenticated and now reachable from the network.
- A microphone becomes publicly addressable, and `worklog` recordings contain employer
  and project detail. NPM access control on the recorder and deletion of inbox blobs on
  `ack` are part of the design, not follow-up work.
- The digest is lossy by construction: no audio, no transcripts, no per-word timings, and
  a horizon on feedback markdown. Offline history is for reading what was said about a
  session, not for replaying it.
- Running the backend at boot is the least certain part. CUDA under a session-0 service
  account is a well-known failure, so `ect doctor` must be verified under whichever
  account Task Scheduler uses before anything is built on top of it.
- Rollout is ordered so that risk comes first: (1) LAN binding, backend at boot, NPM
  host — remote access working with no new code, and the boot question answered; (2) the
  inbox, recorder and drain loop; (3) digest and Wake-on-LAN.
- If the ceiling in the second bullet ever becomes the complaint, the escape route is the
  first rejected alternative, and the digest contract is the thing that would grow into
  it. Nothing here forecloses that.
