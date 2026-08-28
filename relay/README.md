# relay

The server half of remote capture: a switchboard with an inbox, not a second copy of
the app. See [ADR 0006](../docs/adr/0006-remote-capture-local-processing.md) for why,
and [docs/relay.md](../docs/relay.md) for how to run the whole thing.

It stays in this repo rather than getting its own: it deploys to a different host and
shares no dependency with the backend, but its entire contract is defined by this app's
routes, and an inbox schema drifting across two repositories is a worse problem than a
folder Docker builds independently. The k8s manifests do live elsewhere — in
`k8s-config`, with everything else the cluster runs.

## What it is

Go, standard library plus a pure-Go SQLite driver, one static binary.

| File | Role |
| --- | --- |
| `main.go` | route table, handlers, the server |
| `config.go` | every `ECT_RELAY_*` setting |
| `inbox.go` | blob store + `inbox.db` — captures waiting for the PC |
| `digest.go` | the read-only snapshot, and which routes it can answer |
| `proxy.go` | presence tracking and the switchboard |
| `wol.go` | Wake-on-LAN magic packets |

## The routing rule

```
                  PC online              PC offline
GET  /api/…        proxy to PC           answer from digest, or 503 with a reason
POST/PUT/DELETE    proxy to PC           503 {"detail": "PC offline"}
POST /api/inbox    always the inbox      always the inbox
```

`frontend/src/api.ts` already surfaces `detail` on failure, so an offline write
degrades into a readable message with no frontend change. That is also why every error
here uses FastAPI's `detail` key rather than inventing its own.

"Online" means *requests will be answered*, not "the machine has power": it needs a
recent heartbeat whose `api_ok` was true, and a failed proxy attempt marks the PC down
immediately rather than making every subsequent request wait out the timeout.

## Endpoints it owns

Browser-facing (guarded by the ingress basic-auth annotation):

| Route | Purpose |
| --- | --- |
| `GET /api/relay/status` | am I talking to a relay, and is the PC up? The PC's API 404s this, which is how the frontend tells the two apart at runtime. |
| `POST /api/inbox` | one capture: `uid`, `mode`, `topic`, `notes`, then `file` |
| `GET /api/inbox/recent` | "did it arrive?", answerable from the phone that asked |

Agent-facing (bearer `ECT_RELAY_TOKEN`):

| Route | Purpose |
| --- | --- |
| `POST /api/agent/heartbeat` | the PC is up, and its API answers |
| `GET /api/inbox/pending` | what is waiting |
| `GET /api/inbox/{uid}/blob` | the audio |
| `POST /api/inbox/{uid}/ack` | drained — **deletes the blob** |
| `POST /api/inbox/{uid}/fail` | record why, so attempts advance |
| `PUT /api/digest` | store the snapshot |

The field order in `POST /api/inbox` matters: the handler streams the file part
straight to disk, so the fields describing it must arrive first.

## Running it locally

```sh
ECT_RELAY_PC_URL=http://127.0.0.1:8000 \
ECT_RELAY_TOKEN=dev-token \
ECT_RELAY_DATA_DIR=./data \
ECT_RELAY_STATIC_DIR=../frontend/dist \
go run .
```

`ECT_RELAY_STATIC_DIR` is optional — without it the relay serves the API only, which is
what the tests do. With it, `npm run build` in `frontend/` first.

```sh
go test ./...
gofmt -l . && go vet ./...
```

The tests cover the whole switchboard against a real `httptest` PC: the drain cycle,
retried uploads, offline fallback, the three ways the PC can be considered down, and
the auth boundary. No relay, cluster or GPU needed.

## Two things worth knowing before changing it

**The blob is deleted on ack, and that is the design.** A `worklog` recording is full
of employer and project detail, and this server is publicly addressable. The window
between arriving and being drained should be as short as the network allows. The
corollary is that the agent acks *last*, only once the audio is on the PC's disk —
acking any earlier would trade a duplicate for a lost recording.

**The digest is read-only here, on purpose.** `services.write_notes` on the PC has an
optimistic-concurrency contract (load a version, hand it back, 409 if someone moved
first). A writable replica would make that a distributed problem with no obviously
correct merge. A replica that refuses to be written cannot.
