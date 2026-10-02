# 0008 — Update docs for public access

**Status:** blocked
**Type:** chore
**Created:** 2026-10-02
**Parent:** [0001](0001-public-access-cloudflare.md)
**Related:** ADR 0009. Blocked by [0005](0005-go-live-dns-and-end-to-end-checks.md).

## Description

Once the public address works, make the docs say so. Several docs currently say the relay
is reachable from the LAN and the VPN "and from nowhere else". That stops being true.

Done when:

- [ ] ADR 0009 is Accepted, with an "Implemented" date and any changes that did not survive real use.
- [ ] ADR 0006 has a one-line pointer to ADR 0009 at the "internal-only" amendment.
- [ ] `docs/relay.md` covers the public path, the second listener and the new settings.
- [ ] `docs/relay.md` "When something is wrong" has rows for a `403` on the public host and for login loops.
- [ ] `relay/README.md` lists the new settings and has the agent routes fixed.
- [ ] The k8s-config docs are updated (`ect-relay/README.md`, and a new `cloudflared/README.md`).
- [ ] `D:\projects\linux\CREDENTIALS.md` has a short Cloudflare Access section.
- [ ] This repo's `CLAUDE.md` says the public listener exists and `/agent/` must never be on it.

## How to research

- `relay/README.md` lists the agent routes as `/api/agent/heartbeat` and
  `/api/inbox/pending`. The code uses `/agent/heartbeat`, `/agent/inbox/pending`, and so on
  (`relay/main.go` lines 129–135). Fix the table.
- ADR 0006 shows how earlier changes were recorded: inline "Amended" notes with a date.

## How to solve

1. Edit each file in the list above. Keep the existing style.
2. In `CREDENTIALS.md`, point to `credentials.env` and say what each credential can do. Do
   not write any secret value.
3. Do not commit unless asked.
