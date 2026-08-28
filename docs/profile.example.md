# User Profile

**This is the tracked template, not the live profile.** The real one is
`data/profile.md` — copied from this file on first run, gitignored, and never
committed: it fills up with your employer, projects and weaknesses. Edit `data/profile.md`,
not this file.

Read in full by `/generate-topic` so practice topics connect to real work rather than
generic prompts. Kept as prose in markdown rather than in SQLite on purpose (PRD 7.1):
it is small, it is read whole every time, and prose is what it is for.

`/process-session` folds newly inferred facts in there automatically after every session.
**Everything above *Language gaps to target* is additive** - facts about who you are and what
you work on are never rewritten or deleted. *Current work* is grouped by project, so a new
fact joins its project's bullet instead of starting another one.

The last section is the exception: **it is a consolidated summary, not a log.** One line per
live gap, because this file is read *in full* every time a topic is generated. The evidence
and the fix for each gap live in `data/learning-notes.md` - see
`docs/adr/0007-profile-holds-identity-notes-hold-language.md`.

> **Seeding:** everything below is a placeholder. Either fill in what you know now, or
> just start practising - the profile fills itself from what you say. Topics get
> noticeably better once the Role and Current work sections have real content.

---

## Role & background

- Software engineer.
- _(years of experience, seniority, career path — TBD)_

## Employer & team

- _(company, team, product area, team size, who they present to — TBD)_

## Tech stack & domains

- _(languages, frameworks, infrastructure, the domains they actually work in — TBD)_

## Current work

- _(projects in flight, recent launches, current problems being solved — TBD)_

## Communication goals

- Professional English for day-to-day work communication.
- Presentation delivery.
- Interview answering (behavioural and technical).

## Audiences they speak to

- _(standups, design reviews, stakeholder updates, interview panels, customers — TBD)_

## Interests outside work

- _(useful for free-form topics and for warming up — TBD)_

## Language gaps to target

Maintained by `/process-session` as recurring weaknesses show up across sessions. This
section is what makes future topics target real gaps rather than guesses.

**One line per live gap, and nothing else** - no session citations, no examples, no evidence.
This file is read in full on every `/generate-topic` run, so a gap restated once per session
costs its full price every time. The detail belongs in `data/learning-notes.md`; the
per-session evidence is already in `data/feedback/<id>.md`. When a gap is fixed, delete the
line here and record it under *What is working* in the notes.

- _(none recorded yet)_

Strengths are summarised in one short paragraph at the end of this section rather than as
their own list - the full record lives in `learning-notes.md` under *What is working*.
