# 0001 — Reach the trainer from anywhere via Cloudflare

**Status:** in-progress
**Type:** epic
**Created:** 2026-10-02
**Related:** [ADR 0009](../adr/0009-public-access-via-cloudflare-tunnel-and-access.md) (Proposed), ADR 0006

## Description

Today the recorder only works when the phone is on the home network or the WireGuard VPN.
A recording made with the VPN off fails at upload. That stops the app from being the
default way to record on the go.

The goal: open `https://ect.sajitkhadka.com` on the Android phone from anywhere, sign in
with Google, and record. The PC keeps pulling recordings from the relay as it does now.

ADR 0009 explains the design and why. In short: a Cloudflare Tunnel carries the traffic,
Cloudflare Access handles the Google login, and the relay checks the login token itself.
`ect agent` stays on the home network and never uses the public address.

**Scope**
- A public address for the relay, protected by Google login for one person.
- A relay change so it refuses anything that did not come through Access.
- `cloudflared` running in the cluster.
- An installable Android app (PWA) with offline recording.

**Non-goals**
- Moving transcription, scoring or the database off the PC.
- Exposing the PC's API, or any `/agent/` route, to the internet.
- Other services (`jobquest`, `synctodo`, and so on). See 0009.
- A native Android app. Decide that only after using the PWA for a while.

**Children** (suggested order)

- [x] [0002](0002-relay-public-listener-with-access-jwt.md) Add an Access-verified public listener to the relay
- [ ] [0003](0003-create-cloudflare-tunnel-and-access-app.md) Create the Cloudflare tunnel, Access app and policy
- [ ] [0004](0004-deploy-cloudflared-and-wire-the-relay.md) Deploy `cloudflared` and wire the relay in k8s-config
- [ ] [0005](0005-go-live-dns-and-end-to-end-checks.md) Go live: add the DNS record and run end-to-end checks
- [ ] [0006](0006-installable-android-pwa.md) Make the frontend an installable Android PWA
- [ ] [0007](0007-offline-recording-queue.md) Queue recordings offline and retry the upload
- [ ] [0008](0008-update-docs-for-public-access.md) Update docs for public access
- [ ] [0009](0009-spike-expose-other-services-through-the-tunnel.md) Plan exposing other services through the tunnel

0006 and 0007 do not depend on the access work and can start any time. They only become
useful on the phone once 0005 is done.

## Open questions

Ask the owner before 0003. They affect more than one child.

1. **Which Google email may sign in?** Store it as `ACCESS_ALLOWED_EMAIL` in
   `D:\projects\linux\credentials.env`. Do not write it into this repo, which is pushed to GitHub.
2. **Should worklog and journal audio go through Cloudflare?** Cloudflare can see traffic
   in the clear at its edge, and worklogs contain employer and project detail. The choice
   is all modes (default), or only `freeform` and `brainstorm` on the public address.
3. **Keep the hostname `ect.sajitkhadka.com`?** Assumed yes.
4. **Rename the Access team domain** from `sparkling-wood-5caa` to `sajitkhadka`? It only
   changes how the login URL looks. If yes, do it before 0003 and update the Google
   redirect URI to match. Default: keep it.

## Ground rules

Every child follows these.

- Read first: `D:\projects\CLAUDE.md`, this repo's `CLAUDE.md`, ADR 0006, ADR 0009,
  `docs/relay.md`, `relay/README.md`, `D:\projects\deployment\ect-relay\README.md`, and
  `D:\projects\deployment\docs\adr\0003-internal-only-hostnames.md`.
- **Two repos are involved, and both have uncommitted work that is not yours.** This repo,
  and `D:\projects\deployment` (remote `sajitkhadka/k8s-config`). Run `git status` before
  touching either. Do not commit, push or tag unless asked.
- **Apply nothing to the cluster or to Cloudflare unless the owner says so in that
  session.** Read-only API calls are fine. Argo CD is on manual sync. A release tag stages
  a release; it does not ship it.
- **Never print a secret.** Credentials live in `D:\projects\linux\credentials.env`. Load it
  without echoing it:
  ```bash
  set -a; . /d/projects/linux/credentials.env; set +a
  ```
  It holds `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_ZONE_ID`,
  `GOOGLE_CLIENT_ID_ACCESS` and `GOOGLE_CLIENT_SECRET_ACCESS`. Mask secrets in any output
  you show (`sed`).
- The Cloudflare token is **user-owned**. Check it at `GET /client/v4/user/tokens/verify`.
  The `/accounts/{id}/tokens/verify` endpoint says `Invalid API Token` for it. That is
  expected.
- Never put a plain `Secret` manifest in a folder Argo CD watches, not even as an example.
  Real secrets are SealedSecrets. This caused an outage once (see ADR 0006).
- Do not widen the cert-manager token in `linux/CREDENTIALS.md`. It stays DNS:Edit only.

## State on 2026-10-02

Done:
- Zero Trust is on. Team domain: `sparkling-wood-5caa.cloudflareaccess.com`.
- A Google login method named "Google" exists in Access. It uses its own OAuth client, not
  SyncToDo's. Its redirect URI is
  `https://sparkling-wood-5caa.cloudflareaccess.com/cdn-cgi/access/callback`.
  Find its id with `GET /accounts/{acct}/access/identity_providers`. A default
  `cloudflare`-type provider also exists. Do not allow it on the app.
- The API token works for DNS, tunnels, Access apps, service tokens, identity providers
  and the organization.
- Credentials are in `linux/credentials.env`, with a comment header that lists the token's
  permissions. `linux/.gitignore` excludes it.

Not done: no tunnel, no Access app or policy, no DNS record, no `cloudflared`, no relay change.

## Definition of done

0002 to 0005 and 0008 are done, and the end-to-end checks in 0005 all pass. ADR 0009 is
set to Accepted. 0006 and 0007 may be a follow-up if the owner prefers to use the public
address first.
