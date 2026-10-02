# ADR 0009 — Public access to the relay via Cloudflare Tunnel and Access

**Status:** Proposed · **Date:** 2026-10-02 · **Extends:** ADR 0006 · **Supersedes:** the
"internal-only" amendment of 2026-08-28 in ADR 0006's Consequences, and ADR 0006's
dismissal of Cloudflare Tunnel under Alternatives (that dismissal assumed the problem was
reaching a PC on the same LAN; the problem is now reaching the *phone's* side)

Implementation plan: [`docs/tickets/0001-public-access-cloudflare.md`](../tickets/0001-public-access-cloudflare.md).

## Context

ADR 0006 deployed the relay at `ect.int.sajitkhadka.com` and, in its 2026-08-28 amendment,
made it reachable only from the LAN and over WireGuard. That was the right call for a
microphone that records employer and project detail, and it has a cost the amendment
named: **capture needs the tunnel up on the phone.** A commute recording made with
WireGuard off fails at upload instead of queueing. In daily use that is the thing that
keeps the tool from being the default way to record.

The wish is to open the app from the Android phone anywhere, with no VPN step, and to
have the same ability for other self-hosted services later.

Facts that shape the answer:

- **The relay already is the public-edge piece.** The PC is never contacted from outside;
  `ect agent` on the PC pulls from the relay (ADR 0006 sub-decision 2). Nothing about the
  PC's exposure has to change.
- **Behind the relay is an unauthenticated API.** The switchboard proxies the browser's
  `/api` to the PC, including `PUT /api/notes` and `DELETE /api/sessions/{id}`. Whatever
  authenticates the browser is the *only* line of defence for those routes.
- **Hostname obscurity buys nothing here.** `k8s-config` ADR 0003 records `ingress-nginx`
  routing on the `Host` header regardless of DNS, and a scanner requesting a new
  hostname within two minutes of its certificate reaching the CT log. Any public name is
  found immediately; the authentication has to hold on its own.
- **The home IP is already in public DNS** (`jobquest` and `synctodo` are grey-cloud A
  records), and port 443 is forwarded to the cluster.
- **`getUserMedia` needs a secure context** (ADR 0006), so the public name needs a real
  certificate. Cloudflare's edge supplies one with no cert-manager involvement.
- **The frontend is not yet an installable PWA.** It has no web manifest, no service
  worker and no offline queue. "The recorder PWA" in ADR 0006 and `docs/relay.md` means
  "a web page served by the relay". This ADR makes the page reachable; the install and
  offline work is a later phase of the ticket and does not block the decision.
- `auth-platform` is a failed initiative and is not an option for the login story.
- Cloudflare's free plan covers this: DNS, Tunnel and Zero Trust Access (up to 50 users)
  are free, the cap on a proxied request body is 100 MB, and recordings run around
  1 MB per minute of Opus.

## Decision

**Publish the relay on a new public hostname, `ect.sajitkhadka.com`, through a Cloudflare
Tunnel, with Cloudflare Access (Google login, one allowed identity) in front, and keep
`ect agent` on the LAN path it uses today.**

Five parts carry the weight.

**1. A tunnel, not a proxied A record.** `cloudflared` runs in the cluster (two
replicas) and dials *out* to Cloudflare. The public hostname is a proxied CNAME to the
tunnel. The origin accepts public traffic only from `cloudflared`, so there is no
`curl -k https://<home-ip> -H "Host: ect.sajitkhadka.com"` route around Access to defend
with a Cloudflare-IP allowlist. The tunnel is **locally managed**: its ingress rules live
in `k8s-config` and are reviewed and synced like everything else, rather than living in
Cloudflare's dashboard where Argo CD cannot see them.

**2. Authentication at the edge, with no user store.** A Cloudflare Access application
covers `ect.sajitkhadka.com`, with Google as the only identity provider, and one `allow`
policy matching the owner's email. The session lasts 30 days. There is no login code,
no token storage and no database in the relay. Google runs through its **own** OAuth
client created for this purpose, not SyncToDo's: SyncToDo uses Google Identity Services
in the browser with no client secret and no redirect, Access needs the server-side
redirect flow, and sharing a client would couple a change on one to the other.

**3. The agent stays on the LAN.** `ect agent` keeps using `https://ect.int.sajitkhadka.com`
with its bearer token, behind the existing allowlist, exactly as ADR 0006 built it. It
runs on the same network as the server, so it gains nothing from the public path and
would need an Access bypass or service token to use it. The public host therefore has
**no `/agent/` routes at all**: `cloudflared` answers `404` for that prefix, and the
relay's public listener does not register it. The machine credential never becomes
reachable from the internet.

**4. The relay verifies the Access JWT itself.** Access puts a signed
`Cf-Access-Jwt-Assertion` on every request it admits. The relay gets a **second
listener** for tunnel traffic that requires a valid assertion (signature against the team's
JWKS, issuer, audience tag, expiry, and the email claim against an allowlist), and the
tunnel points at that listener, not the existing one. Misconfiguring Access (a policy
deleted, an app on the wrong hostname) then fails closed instead of exposing a proxy to an
unauthenticated API. It must be a *separate* listener rather than a middleware on the
existing one: requests arriving from the LAN through `ingress-nginx` carry no assertion,
and a header-based "is this public?" test could be forged from the LAN.

**5. The internal host is untouched.** `ect.int.sajitkhadka.com` keeps its allowlist,
basic auth, certificate and WireGuard path. It is both the agent's route and the fallback
if Cloudflare is unreachable.

Order of operations is part of the decision: the Access application, its policy and the
relay's JWT check exist **before** the DNS record that makes the name resolve, so there is
never a window in which the hostname is live and unprotected.

## Alternatives

**A proxied A record to the home IP, plus a Cloudflare-IP allowlist on the Ingress.**
Less to run (no `cloudflared`), and works with the port 443 already forwarded. Rejected:
the origin stays reachable by `Host` header from anywhere, so Access is bypassable by
anyone who has the home IP, which is already published. It also keeps the home IP in the
design permanently.

**App-level Google sign-in in the relay.** Keeps everything in one place and works
without Cloudflare Access. Rejected: it means session cookies, CSRF, an allowlist and
token verification written and maintained for a single user, in the one component that
fronts an unauthenticated API. Access does all of it, and the relay still verifies the
result (part 4).

**Reviving `auth-platform`.** Rejected: it was never deployed, no app authenticates
through it, and finishing it is a project far larger than this decision.

**Keep WireGuard only, and make the phone tunnel always-on.** The status quo with a
setting changed. Rejected as the primary route because it still ties capture to a
tunnel that fails silently, and it does nothing for other services. It remains the
fallback.

**Another mesh VPN (Tailscale and similar).** Solves reachability without a public
hostname. Rejected for the same reason as WireGuard: a client must be connected on the
phone, which is the friction being removed.

**Exempt `/agent/` with an Access bypass or a service token, and move the agent to the
public host.** Would let the agent work from outside the home network. Rejected: the
agent runs on the home network and gains nothing, while the bypass would put the machine
credential on the internet.

## Consequences

- **Cloudflare terminates TLS and sees recordings and responses in the clear at its
  edge.** ADR 0006 treated the exposure of `worklog` audio as a design concern (blob
  deleted on ack, internal-only deployment). This reverses part of that on purpose. It is
  the standard cost of any Cloudflare-proxied service and is accepted for the sake of
  access from anywhere. It is the main reason `worklog` capture is listed as an open
  question in the ticket.
- **Remote capture now depends on Cloudflare being reachable, and on Access being
  signed in.** The mitigation is the unchanged internal host over WireGuard.
- **A public hostname exists and will be found.** That is fine only because of Access
  and the relay's JWT check; do not weaken either on the assumption nobody will look.
- **Request bodies through the proxy are capped at 100 MB on the free plan.** The relay's
  `ECT_RELAY_MAX_UPLOAD_BYTES` default is 512 MiB. The public listener gets a lower
  limit so an oversize upload is refused with a readable `detail` before the phone sends
  it, rather than failing partway through at the edge. At roughly 1 MB per minute that
  is close to two hours of speech.
- **An expired Access session is not a normal API failure.** Access answers with a
  redirect to Google's sign-in page, which a script (and any future service worker)
  cannot follow. The frontend must treat an opaque redirect as "sign in again" and do a
  full-page navigation, not retry.
- **`ingress-nginx` is no longer in the public path**, so its `proxy-body-size`,
  timeout and basic-auth settings do not apply to it. The relay's own limits and the
  Access policy replace them.
- **Two credentials now sit in `linux/credentials.env`:** a user-owned Cloudflare API
  token with account-level Tunnel and Access edit rights (broader than the cert-manager
  token, which stays `DNS:Edit` only), and the Google OAuth client for Access. Both are
  rotatable; neither goes in a manifest, only SealedSecrets built from them.
- **`k8s-config` gets a new component** (`cloudflared`) that is shared infrastructure,
  not trainer-specific. Extending it to other services, and closing the forwarded port
  443, is a separate decision and gets its own `k8s-config` ADR; this one covers only
  the trainer relay.
- ADR 0006's statement that the relay "is reachable from the LAN and over the WireGuard
  tunnel, and from nowhere else" stops being true once this ships, and should carry a
  pointer here.
