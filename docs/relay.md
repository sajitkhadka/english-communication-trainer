# Remote capture: the relay and the agent

How to record on a phone and have it become a session on the PC.
[ADR 0006](adr/0006-remote-capture-local-processing.md) is why it is shaped this way;
this file is how to run it.

## The shape

```
  phone                    home server (k3s)                    PC
  ─────                    ─────────────────                    ──
  recorder PWA  ──HTTPS──▶ relay ─┬─ inbox (blobs + inbox.db)
  (the same                       │        ▲                     │
   Vite build)                    │        └── GET pending ───────┤ ect agent
                                  │        └── GET blob ──────────┤  (drain loop)
                                  │        └── POST ack ──────────┤
                                  │                               │
  browsing history ──────▶ switchboard ──LAN──▶ 192.168.0.164:8000 (FastAPI)
                                  │                               │
                                  └─ digest.json ◀── PUT digest ──┘
```

Three moving parts, and only one of them is new code on the PC:

| Piece | Where | Owns |
| --- | --- | --- |
| `relay/` | home server, k3s | recorder PWA, inbox, digest snapshot, the `/api` switchboard |
| `ect agent` | PC, at logon | drain loop, heartbeat, digest push |
| backend + frontend | PC | everything else, unchanged |

**What still needs the PC.** Playback, transcripts, briefs, and every write. The system
degrades to "record and browse", never to nothing and never to "silently processed by
something else". The banner at the top of the app says which state you are in.

## Setting it up

Do these in order. The first two answer the questions most likely to sink the whole
thing, before anything is built on top of them.

### 1. Reach the PC over the LAN

```powershell
./enable-lan-access.ps1 -Report                            # what your addresses are
./enable-lan-access.ps1 -RelayHost 192.168.0.120 -WhatIf   # read this before running it
./enable-lan-access.ps1 -RelayHost 192.168.0.120           # elevated shell
```

Then in `backend/.env`:

```ini
ECT_HOST=0.0.0.0
```

**`0.0.0.0`, not the LAN address.** One uvicorn has to serve both interfaces: the Vite
proxy and `ect agent` reach the API on `127.0.0.1`, the relay reaches it on the LAN
address. Binding only the LAN address breaks the first two — and invites a second
uvicorn onto loopback to fix them, which is the one failure mode `_gpu_lock` cannot
catch. It is a `threading.Lock`, so it guards one process; two processes each loading
`large-v3` do not fit in 6 GB and take the worker down natively, with no exception to
record. (This was not hypothetical during bring-up: binding to the LAN address left two
servers running at once.)

`./dev.ps1` asks pydantic-settings for the value rather than reading `.env` itself, so
env var, `.env` and default keep their normal precedence. It also refuses to start when
the port is already served, which is the guard against exactly the above — do not start
uvicorn by hand alongside it.

Restart the API, then from the server:

```sh
curl http://192.168.0.164:8000/api/health
```

> **The firewall rule is load-bearing.** `PUT /api/notes` and
> `DELETE /api/sessions/{id}` are unauthenticated, and binding off loopback puts them
> on the network. The rule scopes inbound 8000 to the relay host's address alone.
> Do not widen it to the LAN "temporarily".

> **Give the PC a DHCP reservation.** The relay's ConfigMap hard-codes this address. A
> lease that moves breaks remote capture, and the only symptom is the app saying your
> PC is offline, with nothing in any log to explain it.

### 2. Confirm the PC is actually up when you need it

The agent's task runs **at logon, not at boot** — CUDA under a session-0 service
account is a well-known failure, and transcription runs in the API process the agent
drives. Before building on it:

```powershell
./register-agent-task.ps1        # refuses to register without ECT_RELAY_* set
./register-agent-task.ps1 -Status
```

Verify `uv run ect doctor` reports `cuda_available: true` under whatever account the
task uses.

**Wake-on-LAN is not currently wired**, and the reason is in the ConfigMap: this PC is
on Wi-Fi (Intel AX201). Waking a sleeping Wi-Fi NIC is WoWLAN, which most laptops
either do not support or disable on battery. Nothing depends on it — a capture waits
in the inbox until the PC is next up. If the PC is ever wired, set `ECT_RELAY_WOL_MAC`
and verify with a real sleep test before believing it.

### 3. Deploy the relay

Manifests, sealing, DNS: [`k8s-config/ect-relay/README.md`](https://github.com/sajitkhadka/k8s-config)
(locally `D:\projects\deployment\ect-relay\README.md`). The short version:

```sh
# from this repo's root - the build context is the root, not relay/
docker build -f relay/Dockerfile -t 192.168.0.120:5000/ect-relay:latest .
docker push 192.168.0.120:5000/ect-relay:latest
```

Then seal the two Secrets, point `ect.int.sajitkhadka.com` at the cluster, and sync in
Argo CD.

**HTTPS is not optional.** `getUserMedia` requires a secure context; over plain HTTP
the recorder does not merely warn, it fails to start.

Building by hand is the bootstrap path. After that, releases go through CI - see
[Shipping a new relay](#shipping-a-new-relay) below.

### 4. Point the agent at it

`backend/.env`:

```ini
ECT_RELAY_URL=https://ect.int.sajitkhadka.com
ECT_RELAY_TOKEN=<the token sealed into ect-relay-secrets>
ECT_LOCAL_API_URL=http://127.0.0.1:8000
```

```powershell
./register-agent-task.ps1 -Start
```

## Shipping a new relay

`.github/workflows/deploy.yml` fires on a `relay-v*.*.*` tag and does two things:
builds and pushes the image, then commits the new tag into the `k8s-config` repo.

```sh
git tag relay-v0.2.0 && git push origin relay-v0.2.0
```

It stops there on purpose. Argo CD is on manual sync, so a tag *stages* a release;
`ect-relay` shows OutOfSync until someone syncs it at
<https://argo.int.sajitkhadka.com>. Nothing here talks to the cluster directly - an
image set with `kubectl set image` exists nowhere in git, so Argo CD calls it drift
and reverts it on the next sync.

Only the relay ships this way. The backend and agent run on the PC from a git
checkout; that split is the whole point of ADR 0006, and there is no image for them.

Three things have to exist for the workflow to work, and all three are per-repo:

- Actions secrets `REGISTRY_USER`, `REGISTRY_PASSWORD` (the LAN registry login) and
  `K8S_CONFIG_DEPLOY_KEY` (a write-enabled deploy key on `k8s-config`, not a PAT -
  a PAT would carry the whole account)
- a self-hosted runner, `sserver-ect`. The registry is plain HTTP on a private
  address, so only a machine inside the network can push to it. Runners on a personal
  account cannot be shared between repos, so this repo has its own even though
  `sserver` already runs three.

Where each credential lives is recorded in `linux/CREDENTIALS.md`.

**Do not add a plain `Secret` manifest to `k8s-config/ect-relay/`, even as an
example.** Argo CD applies every valid manifest in the app directory, so a template
with a `${PLACEHOLDER}` in it overwrites what the sealed-secrets controller wrote -
and because a running pod never rereads its environment, the damage only appears at
the next restart, as `ect agent` suddenly getting a 401. The Application excludes
`*.example.yaml` for exactly this reason.

## Running it by hand

```bash
cd backend
uv run ect agent status              # can it reach the relay and the local API?
uv run ect agent once                # one drain + digest pass, then exit
uv run ect agent once --no-digest    # drain only
uv run ect agent run --verbose       # the loop, in the foreground
uv run ect agent digest --summary    # what the snapshot costs, without printing it
```

`ect agent once` is the command to reach for when a recording "did not arrive": it does
exactly one pass and prints what it drained, skipped and failed.

## Two credentials, deliberately

| Guards | Mechanism | Where |
| --- | --- | --- |
| browser traffic — recorder, history, the proxy to the PC | ingress-nginx basic auth | `ect-relay-basicauth` Secret |
| `ect agent` — inbox drain, ack, heartbeat, digest push | bearer token | `ECT_RELAY_TOKEN` |

They are separate so the agent's long-lived machine credential is never the one typed
into a phone. The relay refuses to start without a token at all: an unauthenticated
relay is an open door to the PC's unauthenticated API.

## What the phone can and cannot start

Only the four modes that need no prior setup: `freeform`, `worklog`, `brainstorm`,
`journal`. `recommended` and `interview` carry target words chosen by
`/generate-topic`, which is desk work — the relay rejects them at the inbox.

A `journal` recorded on the commute is complete before you sit down: the backend
finalises it the moment transcription finishes. Everything else stops at `recorded`,
exactly as [ADR 0003](adr/0003-queue-based-frontend-to-claude-handoff.md) intended —
you still press "Ready for AI processing" and run the skill yourself.

## When something is wrong

Start with the one question that splits the problem in half: **is the relay up, and
does it think the PC is?**

```bash
curl -u <user>:<pass> https://ect.int.sajitkhadka.com/api/relay/status
```

| Symptom | Where to look |
| --- | --- |
| `last_heartbeat: null` | the agent is not reaching the relay. Token, then outbound path. `ect agent status`. |
| `pc_online: false`, recent heartbeat | the agent is alive but the local API is not answering it. Is uvicorn running? |
| `pc_online: false`, `proxy_failed: true` | the relay could not reach the PC's LAN address. Firewall rule, `ECT_HOST`, DHCP lease. |
| capture stuck, `attempts` climbing | `last_error` on the item says why. `ect agent once` reproduces it with the full traceback on the PC. |
| everything reads fine, writes 503 | working as designed — the PC is offline. |
| recorder will not start | not a secure context. Check you are on `https://`, not an IP. |
| `inbox_pending` climbing, PC awake | the agent is not running. `./register-agent-task.ps1 -Status`. |
| `ect agent status` shows `relay: 401`, and nothing changed on the PC | the relay's copy of the token changed under it. `kubectl -n ect-relay describe secret ect-relay-secrets` - `ECT_RELAY_TOKEN` should be 64 bytes. A shorter one means something applied a template over it. |

Logs: `%LOCALAPPDATA%\ect-agent.log` on the PC,
`kubectl -n ect-relay logs deploy/ect-relay` on the server.

## What the digest does and does not carry

Built by `app/digest.py`, pushed only when its content hash moves, and **never written
to by the relay** — that is what keeps `services.write_notes`' version/409 contract a
local concern rather than a distributed one.

Carried: sessions, scores, the learning notes, vocabulary, progress, the queue, and
feedback markdown for the most recent `ECT_DIGEST_FEEDBACK_HORIZON` (50) sessions.

Not carried, by construction: audio, transcripts, per-word timings, briefs, prompt
files, and feedback prose beyond the horizon. Offline history is for reading what was
said about a session, not for replaying it. Those routes answer 503 with a `detail`
that says so rather than serving a plausible-looking nothing.

## Settings

All `ECT_`-prefixed, all in `backend/.env` (see `app/config.py`):

| Setting | Default | What it does |
| --- | --- | --- |
| `ECT_HOST` | `127.0.0.1` | what the API binds to. Set it to `0.0.0.0` for the relay - **not** the LAN address, which breaks loopback and invites a second uvicorn. |
| `ECT_RELAY_URL` | *(empty)* | the relay. Empty disables the agent entirely. |
| `ECT_RELAY_TOKEN` | *(empty)* | must match the relay's `ECT_RELAY_TOKEN`. |
| `ECT_LOCAL_API_URL` | `http://127.0.0.1:8000` | what the agent drives. |
| `ECT_AGENT_POLL_SEC` | `20` | drain + heartbeat interval. |
| `ECT_AGENT_DIGEST_SEC` | `120` | how often the snapshot is rebuilt (pushed only if changed). |
| `ECT_AGENT_TRANSCRIBE_TIMEOUT_SEC` | `1800` | a drained recording's GPU run is synchronous. |
| `ECT_DIGEST_FEEDBACK_HORIZON` | `50` | sessions carrying full feedback markdown. |

The relay's own settings are `ECT_RELAY_*` in its ConfigMap — see `relay/README.md`.
