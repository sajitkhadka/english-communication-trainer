---
name: log-work
description: Turn recorded worklog sessions (daily spoken work journal) into structured journal entries - projects, decisions with the why, hurdles, wins, competency tags - filed under data/worklog/ for later interview prep and reviews. Use when the user asks to log their day, process a worklog recording, or runs /log-work.
---

# log-work

Turn a spoken end-of-day worklog recording into one structured journal entry. This is
**extraction, not language coaching**: no score, no rubric, no vocabulary updates. The
entry is the material future STAR answers and quarterly reviews are built from, so the
one thing that matters is capturing the *why* behind decisions and the *impact* of
outcomes — exactly what the user will not remember in three months.

All commands run from `backend/`.

## Arguments

| Invocation | Behaviour |
|---|---|
| `/log-work` | process every `worklog` session flagged `pending` |
| `/log-work 12` | process worklog session 12 only |

## 1. Find the work

```bash
cd backend
uv run ect session list --mode worklog --status pending
```

`ect session pending` lists *all* pending sessions — only take the ones whose `mode` is
`worklog`; the rest belong to `/process-session`. If nothing is pending, say so and stop.

## 2. Make sure it is transcribed

If `has_transcript` is false: `uv run ect session transcribe 12`. If it fails, report
the error and move on to the next session.

## 3. Read the brief

```bash
uv run ect session brief 12
```

Ignore the filler/pause annotations — they are for practice sessions. You are reading
for content: what happened, what was decided and why, what went wrong, what shipped.

## 4. Determine the entry date, and merge if it already exists

The entry date is the local calendar day the session was recorded (`created_at` on
`ect session show 12` is UTC — late-night recordings may belong to the previous local
day; when ambiguous, ask nothing and use the local date of `created_at`).

```bash
uv run ect worklog show 2026-08-14   # errors if no entry exists - that's fine
```

If an entry already exists for that date, this recording is an addition to the same
day: merge the new material into the existing sections and submit the **combined**
entry. `ect worklog add` overwrites the day's file — never lose what was already there.

## 5. Write the entry

Write to a scratch file (temp path is fine — the backend owns the canonical location).
Format, sections always present, `-` when there is nothing to report:

```markdown
# 2026-08-14
projects: payment-migration, oncall
tags: debugging, cross-team

## Did
- 2-4 bullets, concrete. What actually happened, not job-description prose.

## Decisions — and why
- Chose X over Y because … ← never omit the why; it is the interview gold.

## Hurdles
- Including how it was (or wasn't) resolved, and who was involved.

## Wins / impact
- With numbers whenever the user said any (latency, count, time saved, revenue).

## Upcoming
- Context for the next entry, not tracked state.
```

Rules:

- **The why and the impact are the payload.** If the user said "I decided to batch the
  writes", keep whatever reason they gave verbatim-adjacent. If they gave numbers, keep
  the numbers exactly.
- **Do not invent.** Nothing goes in the entry that the user did not say. Thin day →
  thin entry; that is honest and fine.
- **Project slugs must stay consistent across days** (`payment-migration`, not
  "the migration" one day and "payments" the next). Check recent entries with
  `uv run ect worklog list --limit 10` and reuse existing slugs.
- **Tags come from the controlled list only** (the backend rejects anything else):
  `leadership, ownership, conflict, ambiguity, cross-team, debugging, design-tradeoff,
  mentoring, failure, delivery, influence`. Tag what the day *evidences*, not what it
  mentions. 1-3 tags is typical; zero is fine.

## 6. File it

```bash
uv run ect worklog add --markdown /path/to/entry.md --date 2026-08-14 \
    --summary "Shipped batched writes for payment-migration; cut p99 40%" \
    --projects "payment-migration,oncall" --tags "debugging,cross-team" --session 12
```

`--summary` is one line and is what listings and future retrieval show — make it carry
the day's headline, not "worked on stuff". This call files the markdown at
`data/worklog/daily/<date>.md`, indexes it, links the session and flips it to
`processed`. Do not use `ect feedback apply` for worklog sessions — the backend refuses
it by design.

## 7. Update the profile

Worklog sessions are the richest source of durable profile facts (employer, team,
projects, stack). Same rules as `/process-session` step 7: read `data/profile.md`
(repo root), fold in genuinely new durable facts, additive only, skip if nothing new.

## 8. Check the rollup backlog

```bash
uv run ect worklog rollup status
```

If `missing` is non-empty, tell the user which completed months have entries but no
rollup yet. Do not generate one unprompted — that is `/worklog-rollup`'s job (Phase 2)
and the user's call.

## 9. Report back

Per session, briefly: the entry date, the one-line summary, projects and tags, whether
it merged into an existing entry, and the file path. Plus the rollup nudge if any.
