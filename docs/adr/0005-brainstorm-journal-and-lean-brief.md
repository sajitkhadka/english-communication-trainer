# ADR 0005 — Brainstorm and journal modes, a lean content-only brief, and titled output files

**Status:** Accepted · **Date:** 2026-08-21 · **Extends:** ADR 0004 (worklog)

## Context

Two more recording types followed the same shape as `worklog` (ADR 0004):

- **`brainstorm`** — think out loud, no target words, no coaching. Needs a skill to
  organise the result into ideas, same relationship to `/process-session` that
  `/log-work` has.
- **`journal`** — daily life, off the record (family, goals, whatever). Unlike every
  other mode, it must **never** be sent to Claude at all — capture only, for the user's
  own reading.

Alongside them, three narrower questions came up that all touch the same seams:

1. `worklog`'s brief already carried filler/pause/target-word sections the skill
   ignored by hand. Adding `brainstorm` would repeat that waste. Where should the
   token-cheap "just the words" view live?
2. Journal existing at all raises a sharper version of a gap that already existed for
   every mode: pressing "Process" transcribes *and* queues for Claude in one click. If a
   session is ever mis-recorded under the wrong mode, or the user simply doesn't want
   this one seen yet, there was no point to intervene between "transcribed" and "sent".
3. Every processed session was filed as `<id>.md` — fine for one at a time, illegible
   once there are fifty. The user wants a short Claude-authored title on both the UI and
   the filename.

## Decisions

**1. A plain-text transcript sibling, not a DB column, drives a mode-aware brief.**
`pipeline/runner.py` writes `data/transcripts/<id>.txt` (just the transcript text)
next to the existing `.json`, matching how every other artefact here works — file on
disk, path or convention in the DB, never an inline blob (`paths.py`). `brief_for_session`
branches on mode: `worklog`/`brainstorm` get a lean render (header + the `.txt` content)
instead of the fully annotated one; `journal` never calls `brief_for_session` at all
(decision 3). The real token saving is *what gets rendered*, not *what gets parsed* —
parsing the JSON server-side is free — so the lean path exists specifically to stop
emitting sections a content-only skill has no use for.

**2. Brainstorm gets its own write-back (`record_brainstorm_entry`), not
`record_feedback`.** `record_feedback` always inserts a `scores` row. `worklog` was
already excluded from it for exactly this reason (services.py: "scoring a journal
rewards performing over reporting", PRD-worklog 5.1) — the same logic applies to an idea
dump. `record_brainstorm_entry` mirrors `record_worklog_entry` minus the parts that
don't apply: no date-based merge, no controlled tags, no rollups. Each brainstorm
session is a standalone capture, not a slice of a continuous journal — that retrieval
need can be added later if it's ever actually felt (same posture ADR 0004 took toward
rollups).

**3. Journal's "never reaches Claude" is a backend invariant, not a UI omission.**
`_transcribe_session` finalises a `journal` session to `processed` the moment
transcription finishes — the `.txt` sibling *is* the entry, `feedback_path` points at
it directly, and the existing feedback-card rendering path (`_feedback_file` resolving
whatever `feedback_path` points to) shows it for free. `services.enqueue` raises if
called on a `journal` session regardless of caller — frontend, CLI, or a future script.
The alternative (leave it to the frontend to just not show a "queue" button) was
rejected: a backend-only guard is the only way "never sent to Claude" is actually true
rather than "not sent by the one client we remembered to check".

**4. Transcribing and queueing became two separate, explicit actions.** The backend
already had the pieces (`POST /transcribe` vs `POST /process?transcribe=false`); they
just weren't exposed as two user gestures. Now "Transcribe" leaves a session at
`recorded`, and a distinct "Ready for AI processing" step is what sets `pending`. This
also gives `journal` a natural place to stop: it only ever shows "Transcribe" and never
develops a second button, because it self-finalises before one would apply.

**5. Mode can be changed after recording, scoped to the four unscored/personal modes**
(`freeform`, `worklog`, `brainstorm`, `journal`), only before a session is `processed`.
`recommended`/`interview` need a topic and often target words set up at creation time, so
they are not switch targets. `change_session_mode` moves the recording file between
mode-scoped folders and invalidates the now-stale transcript/analysis (mode is embedded
in `transcript.json` itself), the same staleness handling `store_recording` already does
for a re-recording.

**6. Every AI-processed output file is titled, including worklog's.** `record_feedback`,
`record_worklog_entry`, and `record_brainstorm_entry` all take an optional `title` (and
`summary`) now, slugified into the filename
(`data/feedback/<id>-<slug>.md`, `data/brainstorm/<id>-<slug>.md`,
`data/worklog/daily/<date>-<slug>.md`) and mirrored onto `sessions.title`/`summary` for
the UI. For worklog specifically this means "does today's entry already exist" can no
longer be answered by guessing a filename from the date — `cmd_worklog_show` and the
same-day merge path now resolve through the DB row (`dbmod.get_worklog_entry`) instead
of `worklog_daily_path` convention, which was already the canonical answer, just unused
for lookups until now. A title that changes between two same-day recordings deletes the
stale file rather than leaving an orphan next to the new one.

## Alternatives considered

**Storing the plain transcript text as a DB column.** Rejected — every other artefact in
this app is a file with a path in the DB, not an inline blob; a text column would be the
only exception for no real benefit (SQLite has no meaningful advantage here, and it
would bypass `paths.py` as the one place that knows where files live).

**A separate `ect session transcript` command instead of a mode-aware `brief`.** Rejected
— it would fork "what Claude reads" into two seams instead of one, and both `/log-work`
and `/process-brainstorm` want the exact same thing `brief` already exists to provide:
the token-cheap view.

**Keeping worklog's filename date-only and relying on `summary` for scannability.**
Considered and explicitly rejected by the user in favour of consistency: every processed
file gets a title in its name, worklog included, even though it costs a DB-backed lookup
instead of a filename guess.

## Consequences

- `/log-work`'s brief shrank as a side effect — it already ignored the annotation
  sections by hand; now they never render at all.
- Adding a fourth or fifth content-only mode later is one line in
  `brief.CONTENT_ONLY_MODES`, not a new brief implementation.
- `journal` is the first mode with no corresponding skill and no `ect <mode> add`
  command — there is nothing to write back, by design.
- Any future "processed" write path must remember the title/summary contract (six
  places, same shape as "Adding a score dimension" in CLAUDE.md) or the UI silently
  falls back to the raw topic and an unslugged filename — safe, but inconsistent with
  the others.
