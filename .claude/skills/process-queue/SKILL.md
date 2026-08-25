---
name: process-queue
description: Process everything currently queued for Claude in one pass, whatever the mode - routes each pending session to the skill that owns it (/process-session, /log-work, /process-brainstorm) and reports one combined summary. Use when the user asks to clear the queue, process everything pending, or runs /process-queue.
---

# process-queue

Clear the whole Claude queue in one pass, whatever is in it.

This skill is a **router, not a processor**. It contains no scoring rules, no entry
format, no `ect feedback apply` / `ect worklog add` / `ect brainstorm add` call, and no
knowledge of what any mode's output should look like. Every session is handed to the
skill that owns it and that skill does the work. If you find yourself about to write a
score payload or a journal entry here, stop - you are in the wrong file, and a second
copy of those rules will drift from the first.

All commands run from `backend/`.

## Arguments

| Invocation | Behaviour |
|---|---|
| `/process-queue` | route and process every session flagged `pending` |
| `/process-queue --plan` | list what is queued and which skill each session would go to, then stop |
| `/process-queue 12` | look up session 12's mode and route just that session |

## 1. Read the queue

```bash
cd backend
uv run ect session pending
```

Each entry carries `id`, `mode`, `topic`, `status` and `has_transcript` - enough to route
on. **Do not read any brief here.** Briefs are the expensive part and each owning skill
reads its own; pulling them in up front costs tokens twice.

If nothing is pending, say so and stop.

For `/process-queue 12`, use `uv run ect session show 12` instead and route that one
session by its `mode`.

## 2. Route by mode

| `mode` | Owner |
|---|---|
| `worklog` | `/log-work` |
| `brainstorm` | `/process-brainstorm` |
| `recommended`, `freeform`, `interview` | `/process-session` |
| `journal` | never appears - see below |

`journal` sessions are finalised to `processed` the moment transcription finishes and
`services.enqueue` refuses them outright, so one cannot reach this queue. If one ever
does, **skip it and report it as a backend bug** - do not route it anywhere. That
invariant is the whole reason the mode exists.

Any other `mode` value is likewise unroutable: skip it, name it in the summary, and do
not guess an owner.

## 3. Dispatch, in this order

Invoke each owning skill **once per non-empty bucket, with no session id**, in this
order:

1. `/log-work` - every pending `worklog` session
2. `/process-brainstorm` - every pending `brainstorm` session
3. `/process-session` - everything else

Each of those already sweeps its own mode's pending sessions, so one invocation per
bucket does the whole bucket. Skip a bucket entirely if the queue has nothing in it -
loading a skill for zero sessions is pure token cost.

The order matters. Worklog and brainstorm are cheap extraction with no shared state;
`/process-session` is the expensive one and the only one that rewrites `data/profile.md`
and `data/learning-notes.md`, so it goes last and gets the queue to itself.

For `/process-queue 12`, invoke the single owning skill with the id instead
(`/log-work 12`, `/process-brainstorm 12`, `/process-session 12`).

Two rules while dispatching:

- **Run buckets sequentially, never in parallel.** Transcription holds a process-wide
  lock (two WhisperX loads do not fit in 6 GB), and `/process-session` and `/log-work`
  both edit `data/profile.md`.
- **If a bucket fails, keep going.** Report what failed and move to the next bucket; a
  broken worklog recording must not cost the user their session feedback.

## 4. Report back

One combined summary, in queue order - the detail already lives in each skill's own
output and in the files it wrote:

| id | mode | outcome |
|---|---|---|
| 12 | recommended | processed - overall 6.8 (+0.4), `data/feedback/12-incident-retro.md` |
| 13 | worklog | 1 entry - `data/worklog/daily/2026-08-25-payment-migration.md` |
| 14 | brainstorm | note - `data/brainstorm/14-offline-sync.md` |
| 15 | freeform | **failed** - transcription error, still `pending` |

Then one line: how many were processed, how many are still pending, and what the user
should do about the failures. Do not restate any skill's feedback, score breakdown, or
entry content - it is already on screen above.
