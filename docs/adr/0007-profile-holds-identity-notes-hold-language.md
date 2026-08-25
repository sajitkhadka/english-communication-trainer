# ADR 0007 — The profile holds identity, the notes hold language

**Status:** Accepted · **Date:** 2026-08-25 · **Refines:** ADR 0001

## Context

`data/profile.md` was specified as append-only (PRD §7.1, and stated in its own header):
*"Additive edits only — nothing here gets rewritten or deleted, so the file becomes more
useful the longer it is used."* `/process-session` folds new facts in after every session
and never removes any.

It is also read **in full** on every `/generate-topic` run. Those two properties are in
direct conflict, and the conflict compounds: the file grows monotonically while the read
cost per run grows with it.

By session 23 the file had reached 27 KB, roughly 6,800 tokens, read whole on every topic
generation *and* every session processing. Its section breakdown showed where the growth
actually was:

| Section | Bullets |
|---|---|
| Language patterns to work on | 32 |
| Strengths to build on | 16 |
| Current work | 19 |
| everything else (identity, employer, stack, goals, interests) | 20 |

The two language sections were 48 of 87 bullets — over half the file — and heavily
redundant. Eight bullets described the same multi-part-prompt drop across sessions 2, 7,
10, 11, 14 and 23. Eight more described run-ons and sentence-length spikes. Five described
vague verbs, four described dropped determiners. Twenty-five bullets for four patterns,
each restatement carrying its own full evidence.

Worse, this duplicated a file that already existed for exactly this purpose.
`data/learning-notes.md` was introduced as the durable coaching record and is
**consolidated, not append-only** — it merges, prunes, and moves fixed items to *What is
working*. Its sections (*Recurring grammar corrections*, *Delivery habits*, *What is
working*) map one-to-one onto the profile's two language sections. Some entries existed
verbatim in both files: the backpressure inversion and the repaired *kick the can down the
road* idiom were each written into both on the same day.

So the system had one consolidated file and one append-only file recording the same
observations, with the append-only one being the one read in full every run.

## Decision

**The profile holds who is speaking. The learning notes hold what has already been taught.
Language detail lives only in the notes.**

Concretely:

1. *Language patterns to work on* and *Strengths to build on* are removed from the profile.
   Their content is merged into `learning-notes.md`, consolidated into its existing
   sections plus two new ones — *Word choice under pressure* and *How answers end* — which
   cover patterns the notes had no home for.
2. The profile gains ***Language gaps to target***: one line per live gap, no session
   citations, no evidence, plus a short strengths paragraph. It exists solely so
   `/generate-topic` can aim the next topic. It is **consolidated, not additive** — the one
   section of the profile that is.
3. Everything above that section stays append-only, because facts about a person and their
   work are not supposed to expire.
4. *Current work* stays append-only in content but is grouped **by project rather than by
   session**. A new fact joins its project's bullet; a shipped project collapses to a line.
   This was the same redundancy in a milder form — ADS was narrated across sessions 4 and
   5, connector health across five separate entries.

## Consequences

The profile went from 27 KB to 14 KB. The notes grew from 11 KB to 16 KB absorbing the
detail. Both files are read by both `/generate-topic` and `/process-session`, so the number
that matters is the combined one: **38 KB to 31 KB, about 2,000 tokens saved per skill
invocation** — and, more importantly, the growth curve is now flat rather than linear,
because the half that grows is the half that gets consolidated.

What we give up: language observations are no longer permanently retained in the order they
were made. That is acceptable because the immutable record already exists — every
observation was written into `data/feedback/<id>.md` first, and those are never rewritten.
The notes are a working summary over an existing audit trail, not the audit trail. This is
the same argument that justified making the notes consolidated in the first place.

The risk is that `/process-session` now has to *delete* from the profile when a gap is
fixed, rather than only adding. A skill that only ever appends will silently refill the
section. The skill's step 7 states the rule explicitly, and both the live file and
`docs/profile.example.md` carry it in their headers, so the constraint is visible at the
point of editing rather than only here.

## Alternatives considered

**Leave it and accept the cost.** The file was still only ~6,800 tokens. Rejected because
the trend is what matters: it had roughly doubled in twenty sessions, with no mechanism
that could ever shrink it. The problem is structural, not a size threshold.

**Move the language sections into SQLite.** ADR 0001 already settled that this content is
prose read whole by a model, not rows queried by a program — and the notes file exists and
already solves it. Adding a third representation would be worse than either.

**Split the profile into two files, `profile-identity.md` and `profile-language.md`.**
This is the same decision with a worse layout: it creates a third hand-edited file with
the same contract as the notes, and the frontend's Notes page (`GET`/`PUT /api/notes`)
would need a second editor for it.

**Summarise the profile at read time instead of at write time.** Have `/generate-topic`
read only the sections it needs. Rejected because the file is small enough that partial
reads save little, and because it leaves the growth unbounded — the next thing to read it
in full inherits the whole problem.
