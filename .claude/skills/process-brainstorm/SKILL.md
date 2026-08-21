---
name: process-brainstorm
description: Organise recorded brainstorming sessions into ideas, open threads, and next steps - extraction only, no coaching, no rubric score. Use when the user asks to process a brainstorm, wants their ideas written up, or runs /process-brainstorm.
---

# process-brainstorm

Turn a spoken brainstorm recording into an organised note. This is **extraction, not
language coaching**: no score, no rubric, no vocabulary updates, no target-word
tracking. The user was thinking out loud, not practising English - the only job is to
capture what they came up with in a form they can actually use later.

All commands run from `backend/`.

## Arguments

| Invocation | Behaviour |
|---|---|
| `/process-brainstorm` | process every `brainstorm` session flagged `pending` |
| `/process-brainstorm 12` | process brainstorm session 12 only |

## 1. Find the work

```bash
cd backend
uv run ect session list --mode brainstorm --status pending
```

Only take sessions whose `mode` is `brainstorm` - the rest belong to `/process-session`
or `/log-work`.

## 2. Make sure it is transcribed

If `has_transcript` is false: `uv run ect session transcribe 12`.

## 3. Read the brief

```bash
uv run ect session brief 12
```

This is the lean, content-only brief (no filler counts, no pause map, no target-word
section) - just the topic and the plain transcript text. Read it for content: what ideas
came up, what's still open, what (if anything) they said they'd actually do next.

## 4. Write the ideas

Write to a scratch file (a temp path is fine - the backend owns the canonical
location). Sections, in order, omit any with nothing to report:

```markdown
## Ideas
- One bullet per distinct idea, cleaned up but in the user's own words. Do not merge
  genuinely separate thoughts into one bullet, and do not invent ideas they did not say.

## Threads worth exploring further
- Open questions the user raised, or ones that clearly fall out of what they said.

## Possible next steps
- Only if the user actually said they'd do something. Never invent action items.
```

## 5. Pick a title and a one-line summary

Both are required. The title becomes part of the output filename and what the UI shows
in place of the raw topic - make it specific to what was actually discussed (e.g. "CLI
tool for log triage", not "Brainstorm session" or "Ideas"). The summary is one line, the
same kind of thing a commit subject line is.

## 6. File it

```bash
uv run ect brainstorm add --session 12 --markdown /path/to/ideas.md \
    --title "CLI tool for log triage" --summary "Sketched a CLI to triage prod logs by service"
```

This stores the markdown at `data/brainstorm/<id>-<slug>.md`, links it to the session,
sets its title/summary, and flips it to `processed`. There is no separate scoring step -
`ect brainstorm add` is the only write path for this mode; do not use
`ect feedback apply` here, it refuses brainstorm sessions on purpose.

## 7. Update the profile (only if something durable came up)

Brainstorms are speculative by nature, so this is optional, unlike `/process-session`'s
required step. If a genuinely durable fact about the user surfaced (a new project, a
tool they're evaluating, a real interest), fold it additively into `data/profile.md`
(repo root). Otherwise skip it and say so.

## 8. Report back

Per session: id, title, one-line summary, how many ideas were captured, and the file
path.
