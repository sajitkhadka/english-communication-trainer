# 0003 — Create the Cloudflare tunnel, Access app and policy

**Status:** done
**Type:** chore
**Created:** 2026-10-02
**Parent:** [0001](0001-public-access-cloudflare.md)
**Related:** ADR 0009

## Description

Done 2026-10-02. Tunnel `home-k3s` id `3bb363a6-021a-4415-bb24-f5c78b9868f7`. Access app "ECT relay" id `9ef41f31-2323-45d2-bd5f-46050e10942d`, `aud` `3bca49e122fee812904e821a05ab2bb1f0516ad942966410e3bb690725e83a14`. One allow policy lists both emails. Recovery copy of the tunnel secret is in `linux/credentials.env`. A first tunnel made by mistake (its secret was lost) was deleted and recreated.

Create the Cloudflare side of the design with the API: a tunnel, an Access application for
`ect.sajitkhadka.com`, and a policy that lets in one person. Do **not** create the DNS
record. That is the last step, in 0005, so the hostname never exists without protection.

Answer the open questions in 0001 first (the allowed email in particular).

Done when:

- [x] A locally-managed tunnel named `home-k3s` exists, and its credentials are stored safely.
- [x] An Access app covers `ect.sajitkhadka.com`, with Google as the only login method.
- [x] Exactly one `allow` policy exists, for the owner's email.
- [x] The app's `aud` tag and the tunnel id are noted for 0002 and 0004.
- [x] No DNS record for `ect` exists yet.

## How to research

- Load credentials as described in 0001. Check the token at `/user/tokens/verify`.
- Read the current Cloudflare API reference for tunnels and Access applications. Field
  names and policy handling change. Access has moved between app-level and reusable policies.
- List the existing login methods and note the id of the one named "Google".

## How to solve

All calls go to `https://api.cloudflare.com/client/v4` with
`Authorization: Bearer $CLOUDFLARE_API_TOKEN`. Show the owner each request body, with
secrets masked, before sending it.

1. Create the tunnel: `POST /accounts/$CLOUDFLARE_ACCOUNT_ID/cfd_tunnel` with
   `{"name": "home-k3s", "config_src": "local", "tunnel_secret": "<base64 of 32 random bytes>"}`.
   Make the secret with `openssl rand -base64 32`. Never print it. Keep the returned tunnel id.
2. Build the credentials file `{"AccountTag": ..., "TunnelID": ..., "TunnelSecret": ...}`.
   It becomes a SealedSecret in 0004. Also save a recovery copy in `linux/credentials.env`.
3. Create the Access app: `POST /accounts/$CLOUDFLARE_ACCOUNT_ID/access/apps` with
   `type: self_hosted`, `name: "ECT relay"`, `domain: "ect.sajitkhadka.com"`,
   `session_duration: "720h"`, `allowed_idps: ["<Google id>"]`,
   `auto_redirect_to_identity: true`, `app_launcher_visible: false`.
   Keep the returned `aud` value.
4. Add the policy: decision `allow`, include the owner's email (from `ACCESS_ALLOWED_EMAIL`).
5. Read the app back. Check it has one policy and that the default Cloudflare login
   method is not allowed.
6. Write the tunnel id and `aud` in a note to the owner. Neither is secret.

Rollback: delete the Access app, then delete the tunnel.
