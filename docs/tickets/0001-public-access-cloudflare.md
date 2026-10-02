# Ticket 0001 — Public access to the relay via Cloudflare Tunnel and Access

**Status:** Ready to start · **Created:** 2026-10-02 · **Decision:**
[ADR 0009](../adr/0009-public-access-via-cloudflare-tunnel-and-access.md) (Proposed — read it first)

**Goal.** Open the English Communication Trainer from the Android phone anywhere, with no
VPN step: `https://ect.sajitkhadka.com`, behind Google login, recordings flowing to the PC
exactly as today.

This ticket is written to be handed to a fresh session with no memory of the one that
produced it. Everything that session learned is below.

---

## 0. Ground rules for whoever picks this up

- Read, in order: `D:\projects\CLAUDE.md`, this repo's `CLAUDE.md`, ADR 0006, ADR 0009,
  `docs/relay.md`, `relay/README.md`, then `D:\projects\deployment\ect-relay\README.md` and
  `D:\projects\deployment\docs\adr\0003-internal-only-hostnames.md`.
- **Two repos are involved and both have uncommitted work that is not yours.**
  `english-communication-trainer` (this one, remote `sajitkhadka/english-communication-trainer`)
  and `D:\projects\deployment` (remote `sajitkhadka/k8s-config`). Do not commit, push or
  tag unless asked. `git status` before touching either.
- **Nothing is applied to the cluster or to Cloudflare without the owner saying so in
  that session.** Read-only API calls are fine. Argo CD is on manual sync; a tag stages a
  release, it does not ship one.
- **Never print a secret.** Credentials are in `D:\projects\linux\credentials.env`
  (gitignored by `linux/.gitignore`; `linux/` is not a git repo). Load without echoing:
  ```bash
  set -a; . /d/projects/linux/credentials.env; set +a
  ```
  Variables present: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_ZONE_ID`,
  `GOOGLE_CLIENT_ID_ACCESS`, `GOOGLE_CLIENT_SECRET_ACCESS`. When showing API responses,
  mask `client_secret`, tunnel secrets and anything token-shaped (`sed`).
- **The Cloudflare token is user-owned.** Verify it at
  `GET /client/v4/user/tokens/verify`. The `/accounts/{id}/tokens/verify` endpoint answers
  `Invalid API Token` for it, which is expected, not a failure.
- Do not put a plain `Secret` manifest in any directory Argo CD watches, not even as an
  example (the 2026-08 incident in ADR 0006). Real secrets are SealedSecrets;
  templates live outside the app path or are matched by `directory.exclude: '*.example.yaml'`.
- Do not widen the cert-manager token (`linux/CREDENTIALS.md`, "Cloudflare"); it stays
  `Zone/DNS/Edit` only.

## 1. State of the world on 2026-10-02

**Done.**
- Zero Trust is enabled on the Cloudflare account. Team domain:
  **`sparkling-wood-5caa.cloudflareaccess.com`** (Cloudflare-generated; not renamed).
- A Google identity provider named **"Google"** exists in Access, built from a dedicated
  OAuth client (not SyncToDo's). Its authorised redirect URI is
  `https://sparkling-wood-5caa.cloudflareaccess.com/cdn-cgi/access/callback`. Look up its
  id with `GET /accounts/{acct}/access/identity_providers` (match on `name`). A default
  `cloudflare`-type provider also exists; **do not allow it on the app.**
- The API token's permissions are confirmed: zone DNS, tunnels, Access apps, service
  tokens, identity providers and organisation all respond.
- Credentials moved to `linux/credentials.env` with a comment header recording the token's
  intended permissions; `linux/.gitignore` created.

**Not done (all of it is yours).** No tunnel, no Access application or policy, no DNS
record for `ect.sajitkhadka.com`, no `cloudflared` in the cluster, no relay code change.

**Relevant facts about the current system.**
- Relay (Go, `relay/`): one listener, `:8080`, behind `ingress-nginx` at
  `ect.int.sajitkhadka.com` (allowlist `192.168.0.0/24,10.66.66.0/24` + basic auth). Agent
  routes live under `/agent/` and are served by a second, annotation-free Ingress. The
  route table is `relay/main.go` ~lines 105–146. `relay/README.md`'s endpoint table still
  lists the agent routes as `/api/agent/...` and `/api/inbox/pending`; the code is
  `/agent/...` — fix the README as part of phase 6.
- Relay Deployment is `strategy: Recreate`, one replica, SQLite on a PVC. Do not change
  that. Service `ect-relay` is ClusterIP `80 -> 8080`.
- `ect agent` runs on the PC and uses `ECT_RELAY_URL=https://ect.int.sajitkhadka.com`. It
  **keeps doing so** (ADR 0009 part 3).
- The PC's API (`:8000`, bound `0.0.0.0`, firewalled to the server) is unauthenticated.
  The relay proxies the browser's `/api` to it. Do not change that boundary.
- The frontend has **no manifest, service worker, or IndexedDB use** (checked). It is not
  an installable PWA yet.
- `jobquest.sajitkhadka.com` and `synctodo.sajitkhadka.com` use `letsencrypt-prod`
  (HTTP-01) and grey-cloud A records to the home IP. Out of scope here (phase 7).

## 2. Open questions — ask the owner before phase 2

1. **Allowed identity.** Which Google account email should the Access policy allow? The
   Claude Code account email is probably it; confirm. Store it as `ACCESS_ALLOWED_EMAIL` in
   `linux/credentials.env` (do not write it into this repo — it is pushed to GitHub).
2. **`worklog` / `journal` through Cloudflare.** Worklog recordings contain employer and
   project detail, and Cloudflare terminates TLS (ADR 0009, Consequences). Accept it for
   all modes, or restrict phone capture on the public host to `freeform` and `brainstorm`
   (a one-line check on the public listener's `POST /api/inbox`)? Default if unanswered:
   all modes, flagged in the ADR.
3. **Hostname.** `ect.sajitkhadka.com` assumed.
4. **Rename the Access team domain** to `sajitkhadka`? Cosmetic. If yes, do it now, before
   anything depends on it, and update the Google redirect URI to match. Default: keep.

## 3. Phase 1 — Relay: public listener with Access JWT verification (trainer repo)

Why first: the relay must refuse unauthenticated traffic on the tunnel's path before the
hostname can resolve to it. Nothing here goes live until phase 4.

Work, in `relay/`:

- `config.go`: new settings, all optional, all `ECT_RELAY_*`:
  - `ECT_RELAY_PUBLIC_ADDR` (e.g. `:8081`; empty = no public listener, today's behaviour)
  - `ECT_RELAY_ACCESS_TEAM_DOMAIN` (`sparkling-wood-5caa.cloudflareaccess.com`)
  - `ECT_RELAY_ACCESS_AUD` (the Access application's AUD tag, from phase 2)
  - `ECT_RELAY_ACCESS_EMAILS` (comma list, allowlist)
  - `ECT_RELAY_PUBLIC_MAX_UPLOAD_BYTES` (default 95 MiB; Cloudflare's free cap is 100 MB)
  - **Fail closed:** if `PUBLIC_ADDR` is set and any of team domain, AUD or emails is
    empty, `LoadConfig` returns an error and the relay does not start (same pattern as the
    existing `ECT_RELAY_TOKEN` check).
- New `access.go`: verify the `Cf-Access-Jwt-Assertion` header.
  - RS256 only. Fetch JWKS from `https://<team domain>/cdn-cgi/access/certs`; cache it, and
    refetch once on an unknown `kid` (keys rotate), with a rate limit so a flood of bad
    `kid`s cannot hammer Cloudflare.
  - Check: signature, `exp`/`nbf` with small skew, `iss == https://<team domain>`,
    `aud` contains the AUD tag, `email` claim (case-insensitive) is in the allowlist.
  - Prefer the standard library (`crypto/rsa`, `crypto/sha256`, `encoding/json`); the relay
    deliberately has almost no dependencies. A JWT library is acceptable if the stdlib
    version grows unwieldy — say so in the PR.
  - Any failure → `403` with a `{"detail": "..."}` body (FastAPI's key, per the relay's
    convention), generic message, specific reason in the log.
- `main.go`: a second `http.Server` on `PUBLIC_ADDR` with a **separate mux** built from the
  same handlers as the first, minus everything under `/agent/` (do not register it; an
  unknown path then 404s, and the existing `/agent/` catch-all must not exist on this
  mux), wrapped by the JWT check, with the lower upload limit. The existing listener is
  unchanged. Do **not** implement this as middleware on the existing listener keyed on
  headers: LAN requests through `ingress-nginx` carry no assertion and any "is this
  public?" header is forgeable from the LAN (ADR 0009 part 4).
  - `GET /api/relay/status` and the static SPA must also sit behind the check.
  - Optional (ask the owner): the `POST /api/inbox` mode restriction from question 2.
- Tests in `relay_test.go` (they run with no cluster or GPU; keep it that way). Generate an
  RSA key in the test and serve a fake JWKS from `httptest`. Cover: no header → 403;
  tampered signature → 403; wrong `aud` → 403; wrong `iss`; expired; email not in
  allowlist → 403; valid → 200; unknown `kid` triggers one refetch; every `/agent/*` path
  → 404 on the public mux (not 401: it must look absent); the internal mux is unchanged
  and still accepts the agent's bearer; oversize upload refused with a readable `detail`;
  config refuses to start with a half-configured public listener.
- Run `go test ./...`, `gofmt -l .`, `go vet ./...` in `relay/`.
- Release with a `relay-v*.*.*` tag **only when the owner asks**; the workflow builds on
  the `sserver-ect` self-hosted runner and commits the image tag into `k8s-config`.

## 4. Phase 2 — Cloudflare: tunnel, Access app and policy (API; nothing resolves yet)

Order matters: **Access app and policy first, DNS record last** (phase 4).

All calls use `https://api.cloudflare.com/client/v4`, header
`Authorization: Bearer $CLOUDFLARE_API_TOKEN`. Show the request body to the owner, with
secrets masked, before each write. Check Cloudflare's current API reference for exact
field names; they change.

1. **Tunnel (locally managed).** `POST /accounts/$CLOUDFLARE_ACCOUNT_ID/cfd_tunnel` with
   `{"name": "home-k3s", "config_src": "local", "tunnel_secret": "<base64 of 32 random bytes>"}`.
   Generate the secret locally (`openssl rand -base64 32`), never print it. From the
   response keep the tunnel id. Build the credentials file `{"AccountTag": "<acct>",
   "TunnelID": "<id>", "TunnelSecret": "<secret>"}` and store it **only** as a SealedSecret
   in phase 3 (and as a line in `linux/credentials.env` as the recovery copy).
2. **Access application.** `POST /accounts/$CLOUDFLARE_ACCOUNT_ID/access/apps`:
   `type: self_hosted`, `name: "ECT relay"`, `domain: "ect.sajitkhadka.com"`,
   `session_duration: "720h"`, `allowed_idps: ["<Google IdP id>"]`,
   `auto_redirect_to_identity: true`, `app_launcher_visible: false`. Record the returned
   **`aud`** — it goes into `ECT_RELAY_ACCESS_AUD`.
3. **Policy.** `allow`, `include: [{"email": {"email": "<ACCESS_ALLOWED_EMAIL>"}}]`. Access
   has moved between app-scoped and reusable policies; use whichever the current API
   expects. Confirm afterwards by `GET`-ing the app and checking there is exactly one
   policy and the default Cloudflare provider is not allowed.
4. Do **not** create the DNS record yet (phase 4, step 2).

## 5. Phase 3 — `cloudflared` in the cluster (`D:\projects\deployment`)

New directory `cloudflared/` and `argocd/applications/cloudflared.yaml`, following the
existing layout and the `ect-relay` Application (manual sync, `directory.exclude:
'*.example.yaml'`). Namespace `cloudflared`.

- **Credentials:** SealedSecret holding the tunnel credentials JSON, built with `kubeseal`
  as `ect-relay/ect-relay-secret.example.yaml`'s header describes. Mount as a file.
- **ConfigMap `config.yaml`** (this is why the tunnel is locally managed):
  ```yaml
  tunnel: <tunnel id>
  credentials-file: /etc/cloudflared/creds/credentials.json
  ingress:
    - hostname: ect.sajitkhadka.com
      path: ^/agent(/|$)
      service: http_status:404
    - hostname: ect.sajitkhadka.com
      service: http://ect-relay.ect-relay.svc.cluster.local:8081
    - service: http_status:404
  ```
  Belt and braces: the relay's public mux also lacks `/agent/`, so this 404 is a second
  layer, not the only one.
- **Deployment:** 2 replicas, pinned `cloudflare/cloudflared` image tag (not `latest`),
  `args: [tunnel, --no-autoupdate, --config, /etc/cloudflared/config.yaml, --metrics,
  0.0.0.0:2000, run]`, liveness/readiness on `/ready` at `:2000`, small resource requests,
  `regcred` not needed (public image). Run as non-root.
- **ect-relay changes** in `D:\projects\deployment\ect-relay\`: add container port `8081`
  and Service port `8081` (name `public`); set `ECT_RELAY_PUBLIC_ADDR`,
  `ECT_RELAY_ACCESS_TEAM_DOMAIN`, `ECT_RELAY_ACCESS_AUD`, `ECT_RELAY_ACCESS_EMAILS` in the
  ConfigMap (the email is an identifier, not a secret, but it is in a pushed repo — put it
  in the existing `ect-relay-secrets` SealedSecret instead if the owner prefers). Leave
  both existing Ingresses untouched. Deploy order: relay with the new listener first,
  `cloudflared` second.
- Dry run: `kubectl apply --dry-run=client --validate=strict -f` against the live cluster,
  as the ect-relay README did. Then wait for the owner before syncing in Argo CD.

## 6. Phase 4 — Prove it is closed, then open it

Before the DNS record exists, from inside the cluster (`kubectl run` a curl pod, or port
forward):

- `curl http://ect-relay.ect-relay.svc:8081/api/relay/status` with **no** header → `403`.
- Same with a forged `Cf-Access-Jwt-Assertion: x.y.z` → `403`.
- `curl .../agent/status` → `404`.

Then create the record (the owner confirms): `POST /zones/$CLOUDFLARE_ZONE_ID/dns_records`
`{"type":"CNAME","name":"ect","content":"<tunnel id>.cfargotunnel.com","proxied":true}`.

Verify from outside (phone on mobile data, **WireGuard off**):
- [ ] `curl -I https://ect.sajitkhadka.com` → redirect to `sparkling-wood-5caa.cloudflareaccess.com` (login), not an app response.
- [ ] Signing in with the allowed Google account lands on the app; a different Google account is refused.
- [ ] `https://ect.sajitkhadka.com/agent/status` → `404`, signed in or not.
- [ ] A `freeform` recording uploads, appears in `GET /api/inbox/recent`, and
      `ect agent once` on the PC drains and transcribes it.
- [ ] `ect agent status` still reports healthy via `ect.int.sajitkhadka.com`.
- [ ] `https://ect.int.sajitkhadka.com` still works from the LAN with basic auth (nothing regressed).
- [ ] An upload just over 95 MiB is refused with a readable message.
- [ ] Revoke the session in Zero Trust → next load goes back to login.

**Rollback** (any step, fastest first): delete the CNAME record; scale `cloudflared` to 0;
delete the Access application. The internal host is unaffected by all of it.

## 7. Phase 5 — Make it a real Android PWA (trainer `frontend/`)

Separate from the access work and can follow it; the public URL is useful on its own.

- Web app manifest (name, icons incl. maskable, `display: standalone`, `start_url`,
  theme colours from the CSS custom properties — the colour rules in `CLAUDE.md` apply).
- Service worker: cache the app shell; serve the digest `GET`s stale-while-revalidate so
  history is readable offline. Never cache `POST`/`PUT`/`DELETE`, and never cache an
  opaque redirect response.
- **Access expiry.** Fetch with `redirect: "manual"`; an `opaqueredirect` (or a
  cross-origin response from `cloudflareaccess.com`) means the session ended. Show "Sign in
  again" and do a full-page navigation. A silent retry loop is the failure to avoid.
- **Offline recording queue** in IndexedDB: the recorder already mints the `uid`
  (`sessions.external_uid`), so retries are idempotent end to end. Queue the blob, retry
  with backoff and on `online`, delete it only on a confirmed upload. Show queue state.
- Keep the screen awake while recording (`navigator.wakeLock`) and test what Chrome on
  the owner's Android does on screen lock and app switch. This is the main reason a
  Capacitor wrapper might ever be needed; decide after real use, not before.
- `npm run typecheck` and `npm run build` must pass (there is no frontend test suite).

## 8. Phase 6 — Documentation

- ADR 0009: set Status to Accepted when phase 4 passes; add an "Implemented" date and any
  amendments that did not survive contact (ADR 0006 did this inline; follow that shape).
- ADR 0006: add a one-line pointer to ADR 0009 at the "internal-only" amendment.
- `docs/relay.md`: the public path, the second listener, the new settings, a row in "When
  something is wrong" for a `403` from the public host and for Access login loops.
- `relay/README.md`: new settings; **fix the agent route table** (`/agent/...`).
- `D:\projects\deployment\ect-relay\README.md` and a new `cloudflared/README.md`.
- `D:\projects\linux\CREDENTIALS.md`: a short Cloudflare Access section pointing at
  `credentials.env` (no secret values) and recording what each credential can do.
- `CLAUDE.md` (this repo): one line under the relay section about the public listener and
  that `/agent/` must never be exposed on it.

## 9. Phase 7 — Later, not part of this ticket

Other services on subdomains through the same tunnel, and closing the forwarded port 443.
Needs its own `k8s-config` ADR first. Things already known:
- `jobquest` and `synctodo` use HTTP-01 certificates, which need ports 80/443 inbound;
  moving them behind the tunnel removes that need, but check certificate renewal first.
- SyncToDo has an MCP server (`mcp.synctodo...`) and an OAuth ingress used by
  non-browser clients. Access cannot sit in front of those (no interactive login); they
  need their own auth as today, or a service token.
- Keep `immich-app`, the home-server admin pages and Argo CD on the VPN. Cloudflare's
  free plan restricts bulk photo and video through the proxy, and those are high-value
  targets.
- WireGuard (UDP) cannot go through the tunnel; its port stays open.

## 10. Definition of done

Phases 1–4 complete and the verification list in phase 4 all ticked; ADR 0009 Accepted;
docs in phase 6 updated; nothing committed or pushed except at the owner's request.
Phase 5 (PWA) may be a follow-up ticket if the owner prefers to use the public URL first.
