# 0009 — Plan exposing other services through the tunnel

**Status:** open
**Type:** spike
**Created:** 2026-10-02
**Parent:** [0001](0001-public-access-cloudflare.md)
**Related:** ADR 0009, `k8s-config` ADR 0003

## Description

Once the trainer works through the tunnel, the owner wants other services on their own
subdomains the same way. That would also hide the home IP and let the router stop
forwarding port 443. The output of this spike is a written recommendation and a
`k8s-config` ADR. Do not move any service.

Done when:

- [ ] There is a list of services, each marked: tunnel with Access, tunnel without Access, or stay on the VPN.
- [ ] The certificate question is answered (below).
- [ ] A draft ADR exists in `D:\projects\deployment\docs\adr\`.
- [ ] The owner has decided whether to close port 443.

## How to research

Facts already known (2026-10-02):

- `jobquest.sajitkhadka.com` and `synctodo.sajitkhadka.com` use the `letsencrypt-prod`
  issuer. That is HTTP-01, which needs ports 80 and 443 open from the internet. Behind a
  tunnel, Cloudflare's certificate serves visitors, so the origin may not need one.
  Check what renews and what breaks.
- Their DNS records are grey-cloud A records to the home IP. Moving them to the tunnel
  hides the IP. Remove the old records afterwards.
- SyncToDo has an MCP server (`mcp.synctodo...`) and an OAuth ingress for tools that
  cannot do a browser login. Access cannot sit in front of those. They need their own
  auth as today, or an Access service token.
- Keep these on the VPN: `immich-app` (Cloudflare's free plan restricts bulk photo and
  video through its proxy), the home-server admin pages, and Argo CD.
- WireGuard uses UDP and cannot go through the tunnel. Its port stays open.
- The `*.int` hostnames do not use the public path, so closing 443 does not affect them.
- Cloudflare becomes a single point of failure for anything moved. Two `cloudflared`
  replicas help with pod restarts, not with a Cloudflare outage.

Find out: which services have their own login and other users, and so should not get an
Access login in front.

## How to solve

1. List every public ingress in `D:\projects\deployment`.
2. Mark each one using the facts above, and ask the owner about any that are unclear.
3. Write the draft ADR in `k8s-config`. Compare the alternatives: tunnel for everything,
   tunnel for some, or leave as is.
4. File follow-up tickets (one per service) with the create-ticket skill.
