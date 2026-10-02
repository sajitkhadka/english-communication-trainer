# 0002 — Add an Access-verified public listener to the relay

**Status:** open
**Type:** feature
**Created:** 2026-10-02
**Parent:** [0001](0001-public-access-cloudflare.md)
**Related:** ADR 0009

## Description

The relay needs a second listener for traffic that comes through the Cloudflare Tunnel.
That listener must reject any request that does not carry a valid Cloudflare Access login
token. Then, if Access is ever set up wrong, the public address fails closed. It does not
hand out a proxy to the PC's unauthenticated API.

Today the relay has one listener (`:8080`) behind `ingress-nginx`. It stays as it is.

Done when:

- [ ] A new public listener starts only when its settings are all present, and refuses to start if they are half set.
- [ ] Every request to it needs a valid `Cf-Access-Jwt-Assertion` header, or gets `403`.
- [ ] No `/agent/` route exists on it. Those paths return `404`.
- [ ] Uploads over the public limit get a readable `detail` message.
- [ ] The existing listener behaves exactly as before.
- [ ] `go test ./...`, `gofmt -l .` and `go vet ./...` pass in `relay/`.

## How to research

- `relay/main.go` lines 105–146 hold the route table. The agent routes are under `/agent/`.
- `relay/config.go` holds all `ECT_RELAY_*` settings. `LoadConfig` already refuses to start
  without `ECT_RELAY_TOKEN`. Follow that pattern.
- `relay/relay_test.go` tests the whole relay against a fake PC. It needs no cluster or GPU.
  Keep it that way.
- Cloudflare's current docs for validating the Access JWT. The key set is at
  `https://<team domain>/cdn-cgi/access/certs`.

## How to solve

This is a suggestion. Deviate if you find a better way.

1. Add settings in `config.go`, all optional:
   - `ECT_RELAY_PUBLIC_ADDR` (for example `:8081`; empty means no public listener)
   - `ECT_RELAY_ACCESS_TEAM_DOMAIN` (`sparkling-wood-5caa.cloudflareaccess.com`)
   - `ECT_RELAY_ACCESS_AUD` (the Access app's AUD tag, from 0003)
   - `ECT_RELAY_ACCESS_EMAILS` (comma-separated allowlist)
   - `ECT_RELAY_PUBLIC_MAX_UPLOAD_BYTES` (default 95 MiB; Cloudflare's free plan caps a request at 100 MB)
   If the public address is set and any of the other three are empty, `LoadConfig` returns an error.
2. Add `access.go` to verify the header. Accept RS256 only. Check the signature, `exp` and
   `nbf` (small clock skew allowed), `iss` is `https://<team domain>`, `aud` contains the
   AUD tag, and the `email` claim is in the allowlist. Prefer the standard library. Say so
   in the review if you add a JWT package.
3. Cache the key set. Refetch once when a token has an unknown `kid`, because keys rotate.
   Rate-limit that refetch so bad tokens cannot flood Cloudflare.
4. In `main.go`, build a **separate mux** for the public server. Reuse the same handlers.
   Leave out everything under `/agent/`, including the `/agent/` catch-all. Wrap the mux in
   the JWT check. Include `GET /api/relay/status` and the static app.
5. Do not add this as middleware on the existing listener. Requests from the LAN through
   `ingress-nginx` carry no token, and a header that says "this is public" could be forged
   from the LAN.
6. Failures return `403` with `{"detail": "..."}`, as the rest of the relay does. Keep the
   message generic. Log the real reason.
7. Tests (use a generated RSA key and a fake key-set server): no header, bad signature,
   wrong `aud`, wrong `iss`, expired, wrong email (all `403`); valid token (`200`);
   unknown `kid` causes one refetch; every `/agent/*` path is `404` on the public mux;
   the existing mux still accepts the agent's bearer token; an oversize upload gets a
   readable `detail`; half-set config refuses to start.
8. Ask the owner about question 2 in 0001. If they want it, add a mode check on the public
   `POST /api/inbox`.
9. Do not tag a release. The owner decides when (`relay-v*.*.*`).
