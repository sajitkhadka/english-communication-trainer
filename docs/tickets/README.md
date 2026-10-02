# Tickets

Work items for this repo. One file per ticket: `NNNN-title.md`. Status values: open, in-progress,
blocked, done, wontfix. Decisions that were genuinely contested go in `docs/adr/` instead.

| # | Title | Type | Status |
| --- | --- | --- | --- |
| [0001](0001-public-access-cloudflare.md) | Reach the trainer from anywhere via Cloudflare | epic | in-progress |
| [0002](0002-relay-public-listener-with-access-jwt.md) | ↳ Add an Access-verified public listener to the relay | feature | done |
| [0003](0003-create-cloudflare-tunnel-and-access-app.md) | ↳ Create the Cloudflare tunnel, Access app and policy | chore | done |
| [0004](0004-deploy-cloudflared-and-wire-the-relay.md) | ↳ Deploy `cloudflared` and wire the relay in k8s-config | chore | done |
| [0005](0005-go-live-dns-and-end-to-end-checks.md) | ↳ Go live: add the DNS record and run end-to-end checks | chore | open |
| [0006](0006-installable-android-pwa.md) | ↳ Make the frontend an installable Android PWA | feature | in-progress |
| [0007](0007-offline-recording-queue.md) | ↳ Queue recordings offline and retry the upload | feature | in-progress |
| [0008](0008-update-docs-for-public-access.md) | ↳ Update docs for public access | chore | blocked |
| [0009](0009-spike-expose-other-services-through-the-tunnel.md) | ↳ Plan exposing other services through the tunnel | spike | in-progress |
