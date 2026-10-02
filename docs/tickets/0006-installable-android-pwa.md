# 0006 — Make the frontend an installable Android PWA

**Status:** open
**Type:** feature
**Created:** 2026-10-02
**Parent:** [0001](0001-public-access-cloudflare.md)
**Related:** ADR 0009

## Description

The frontend is a normal web page. It has no web manifest, no service worker and no
IndexedDB use. So it cannot be installed on an Android phone, and history is not readable
offline. (Checked on 2026-10-02: nothing in `frontend/` matches those names.)

Make it installable, make the app shell and history load offline, and handle an expired
Cloudflare Access login without a retry loop.

Done when:

- [ ] Chrome on Android offers "Install app", and the installed app opens full screen.
- [ ] With the phone offline, the app opens and shows cached history.
- [ ] Offline writes show a clear message. Nothing is cached for `POST`, `PUT` or `DELETE`.
- [ ] When the Access session ends, the app shows "Sign in again" and a tap goes to the login page.
- [ ] `npm run typecheck` and `npm run build` pass in `frontend/`.

## How to research

- `frontend/src/api.ts` uses `const BASE = "/api"`. All calls go through it.
- The relay serves the Vite build and answers `GET /api/relay/status`. The frontend uses
  that to tell the relay from the PC.
- The digest routes (read-only history) are what can safely be cached.
- Colours come from CSS custom properties in `src/styles.css`. Do not hard-code hex values.
- Decide how to register the service worker with the Vite setup (a plugin or a hand-written file).

## How to solve

This is a suggestion.

1. Add a manifest: name, icons (including a maskable one), `display: standalone`,
   `start_url`, theme colours from the existing palette.
2. Add a service worker. Cache the app shell. Use stale-while-revalidate for history `GET`s.
   Never cache writes, and never cache a redirect response.
3. Detect an ended Access session. Fetch with `redirect: "manual"`. An `opaqueredirect`
   result, or a response from `cloudflareaccess.com`, means "signed out". Show the message
   and use a full-page navigation to log in again. Do not retry in a loop.
4. Test on the owner's Android phone once 0005 is done. Before then, test over the LAN.
