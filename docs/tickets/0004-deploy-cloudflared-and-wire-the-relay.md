# 0004 — Deploy `cloudflared` and wire the relay in k8s-config

**Status:** blocked
**Type:** chore
**Created:** 2026-10-02
**Parent:** [0001](0001-public-access-cloudflare.md)
**Related:** ADR 0009. Needs the tunnel id and `aud` tag from [0003](0003-create-cloudflare-tunnel-and-access-app.md), and the new listener from [0002](0002-relay-public-listener-with-access-jwt.md).

## Description

Blocked: needs the tunnel id, credentials and `aud` tag from 0003.

Run `cloudflared` in the cluster and point it at the relay's new public listener. This is
done in the manifests repo, `D:\projects\deployment` (remote `k8s-config`). Argo CD is on
manual sync, so nothing changes in the cluster until the owner syncs it.

Done when:

- [ ] A `cloudflared` app runs with two replicas and passes its readiness check.
- [ ] The tunnel's rules live in git and return `404` for `/agent`.
- [ ] The relay exposes the new port and has the new settings.
- [ ] The two existing relay Ingresses are unchanged.
- [ ] All manifests pass a strict client dry run against the live cluster.

## How to research

- `D:\projects\deployment\ect-relay\` for the layout to copy, and its README.
- `argocd/applications/ect-relay.yaml` for the Application shape (manual sync, and
  `directory.exclude: '*.example.yaml'`).
- `ect-relay/ect-relay-secret.example.yaml` header for how the repo seals secrets.
- Check which `cloudflare/cloudflared` release is current and pin it.

## How to solve

1. Add `cloudflared/` and `argocd/applications/cloudflared.yaml`. Use namespace `cloudflared`.
2. Seal the tunnel credentials JSON as a SealedSecret and mount it as a file. Never commit
   a plain Secret.
3. Put the tunnel's rules in a ConfigMap. This is why the tunnel is locally managed:
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
   The relay's public listener also has no `/agent/` routes. This rule is a second layer.
4. Deployment: two replicas, a pinned image tag (not `latest`), run as non-root,
   `args: [tunnel, --no-autoupdate, --config, /etc/cloudflared/config.yaml, --metrics, 0.0.0.0:2000, run]`,
   liveness and readiness on `/ready` at port 2000, small resource requests.
5. In `ect-relay/`: add container port 8081 and a Service port 8081 named `public`. Add
   `ECT_RELAY_PUBLIC_ADDR`, `ECT_RELAY_ACCESS_TEAM_DOMAIN`, `ECT_RELAY_ACCESS_AUD` and
   `ECT_RELAY_ACCESS_EMAILS` to the ConfigMap. If the owner does not want their email in a
   pushed repo, put it in the existing `ect-relay-secrets` SealedSecret instead.
6. Leave `ect-relay-ingress.yaml` and `ect-relay-agent-ingress.yaml` alone.
7. Run `kubectl apply --dry-run=client --validate=strict -f` on everything.
8. Order when the owner syncs: the relay first (with the new image from a `relay-v*` tag
   of 0002), then `cloudflared`.
