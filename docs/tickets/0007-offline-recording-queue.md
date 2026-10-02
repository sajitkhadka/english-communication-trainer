# 0007 — Queue recordings offline and retry the upload

**Status:** open
**Type:** feature
**Created:** 2026-10-02
**Parent:** [0001](0001-public-access-cloudflare.md)
**Related:** ADR 0006, ADR 0009. Works best after [0006](0006-installable-android-pwa.md).

## Description

If the phone has no connection when a recording ends, the upload fails and the recording is
at risk. Keep the recording on the phone until the relay confirms it, and retry later.

This is safe to retry. The recorder creates the session id (`sessions.external_uid`), and
the relay and agent already treat a repeated upload as the same session.

Done when:

- [ ] A recording made offline is saved on the phone and shows as "waiting to upload".
- [ ] It uploads on its own when the connection returns, and after the app restarts.
- [ ] It is deleted from the phone only after the relay confirms it.
- [ ] A failed upload is retried with a growing delay, and the last error is visible.
- [ ] An upload that hits an ended Access session asks the user to sign in, then continues.
- [ ] The same recording never creates two sessions.

## How to research

- How the recorder uploads today, and where it creates the `uid`.
- `POST /api/inbox`: the fields come first and the file last (the relay streams the file to disk).
- `GET /api/inbox/recent` answers "did it arrive?".
- Check what Chrome on the owner's Android does to a recording when the screen locks or the
  app goes to the background. If it stops, try `navigator.wakeLock` while recording. This
  is the main reason a native wrapper (Capacitor) might ever be needed. Decide that after
  real use, not before.

## How to solve

This is a suggestion.

1. Store the recording blob and its fields in IndexedDB as soon as recording stops.
2. Upload from the stored copy. Retry with backoff, and also on the browser's `online` event.
3. Delete the stored copy only after a confirmed success response.
4. Show the queue: what is waiting, what failed, and why.
5. Treat an opaque redirect as "signed out", the same way as in 0006.
