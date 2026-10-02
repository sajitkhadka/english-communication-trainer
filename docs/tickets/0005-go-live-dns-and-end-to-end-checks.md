# 0005 — Go live: add the DNS record and run end-to-end checks

**Status:** blocked
**Type:** chore
**Created:** 2026-10-02
**Parent:** [0001](0001-public-access-cloudflare.md)
**Related:** ADR 0009. Blocked by [0004](0004-deploy-cloudflared-and-wire-the-relay.md).

## Description

Make `ect.sajitkhadka.com` resolve, and prove that it is closed to everyone but the owner
before relying on it. Prove the closed state first. Then add the record. Then test from a
phone on mobile data with the VPN off.

Done when every box below is ticked:

- [ ] Inside the cluster, the public listener returns `403` with no header.
- [ ] Inside the cluster, it returns `403` with a forged `Cf-Access-Jwt-Assertion: x.y.z`.
- [ ] Inside the cluster, `/agent/status` on it returns `404`.
- [ ] `curl -I https://ect.sajitkhadka.com` redirects to the Access login. It does not return the app.
- [ ] Signing in with the allowed Google account opens the app. Another Google account is refused.
- [ ] `https://ect.sajitkhadka.com/agent/status` returns `404`, signed in or not.
- [ ] A `freeform` recording from the phone shows in `GET /api/inbox/recent`.
- [ ] `ect agent once` on the PC drains and transcribes it.
- [ ] `ect agent status` is still healthy through `ect.int.sajitkhadka.com`.
- [ ] `ect.int.sajitkhadka.com` still works from the LAN with basic auth.
- [ ] An upload just over 95 MiB is refused with a readable message.
- [ ] Revoking the session in Zero Trust sends the next page load back to login.

## How to research

- Confirm 0004 is synced and both `cloudflared` replicas are ready
  (`kubectl -n cloudflared get pods`).
- Confirm the relay pod runs the image from 0002 and has the public port.

## How to solve

1. Run the first three in-cluster checks with a throwaway curl pod. Do this **before** the DNS record.
2. With the owner's go-ahead, create the record:
   `POST /zones/$CLOUDFLARE_ZONE_ID/dns_records` with
   `{"type": "CNAME", "name": "ect", "content": "<tunnel id>.cfargotunnel.com", "proxied": true}`.
3. Run the rest of the checks. Use the phone on mobile data with WireGuard off.
4. If anything is wrong, roll back at once: delete the CNAME, scale `cloudflared` to 0, then
   delete the Access app. The internal host is not affected by any of these.
5. When all pass, tell the owner. They can then set ADR 0009 to Accepted (see 0008).
