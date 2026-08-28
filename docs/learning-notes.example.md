# Learning Notes

**This is the tracked template, not the live file.** The real one is
`data/learning-notes.md` — copied from this file on first run, gitignored, and never
committed. Edit `data/learning-notes.md`, not this file.

This is the durable half of the feedback loop. `data/feedback/<id>.md` is per-session
and you will never reread session 12; this file is what survives it — the patterns,
phrases and corrections worth carrying into the next session. `/process-session` reads
it before coaching, so it stops repeating advice you have already absorbed, and folds
new lessons back in afterwards.

Deliberately **not** part of `data/profile.md`: the profile answers *who is speaking*
and is read in full on every `/generate-topic` run, so it has to stay short. This file
answers *what has already been taught*, and it is meant to grow.

Unlike the profile, this file is **consolidated, not append-only**. Three notes saying
the same thing about articles should become one better note, and a lesson you have
genuinely internalised should move to *What is working* or come out entirely. A file
that only grows stops being read, which defeats the point of having one.

---

## Sentence patterns

The small set of structural frames worth having as muscle memory. Five to seven is the
sweet spot: too few and every answer comes out the same shape, too many and choosing
between them costs you the fluency you were buying. Speaking and writing use the same
frames — writing just allows a denser vocabulary inside them.

### Point + Reason + Example

`[Statement] because [reason]; for instance, [example].`

> "We should prioritise the API work because latency is hurting checkout; for instance,
> those requests are over two seconds today."

### Contrast & Pivot

`While [A], the real focus should be [B].`

> "While the short-term fix buys us time, the real focus should be the retry logic."

### Condition & Outcome

`If we [action], we can expect [outcome], which lets us [broader impact].`

> "If we automate the release checks, we can expect to save a day a week, which lets us
> spend that time on the migration."

### Acknowledge & Reframe — for pushback

`[Acknowledge the valid point]; however, from a [longer-term] perspective, [argument].`

> "I see the case for shipping this week; however, from a reliability perspective,
> refactoring now is what stops us paying for it every sprint."

### Option Comparison — for decisions

`Rather than [A], a more [sustainable] route would be [B], because [reason].`

> "Rather than patching the legacy service, a more sustainable route would be moving to
> the new API, because it handles the load we are actually expecting."

### Cause & Escalation — for incidents

`[Event] led to [second-order issue], which ultimately resulted in [outcome].`

> "The connection pool timed out, which led to requests queueing, and ultimately
> resulted in checkout dropping calls."

### Recommendation + Impact — for proposals

`I recommend [action] so that we can [goal] and avoid [risk].`

> "I recommend adding integration tests before the release so that we can cover the
> edge cases and avoid another unplanned rollback."

**Which of these are actually landing** — maintained by `/process-session`, from what
shows up in real recordings rather than from what has been read here:

- _(nothing recorded yet)_

## Phrases and connectors to activate

Vocabulary that is recognised but not yet produced. `ect vocab gaps` measures this
deterministically — the `dormant` bucket is the corpus half of it, and this section is
for the phrases and connectors worth a deliberate push. Keep it short: a phrase comes
off this list the session it shows up unprompted.

- _(nothing recorded yet)_

## Recurring grammar corrections

The same error seen across sessions, with the rule that fixes it. One line each, and
only once it has happened twice — a single slip is an incident, not a pattern.

- _(nothing recorded yet)_

## Delivery habits

Filler and hesitation patterns, pace, where the pauses fall. What repeats across
sessions, not the counts for any one of them — those live in the per-session feedback.

- _(nothing recorded yet)_

## Word choice under pressure

Reaching for a general-purpose word when a precise one exists - vague verbs, vague
quantities, vague time commitments, hedges used as stalls. The trigger is usually
retrieval: the precise word has not arrived, so a placeholder goes in.

- _(none recorded yet)_

## How answers end

Endings are a separate skill from the rest of the answer and are often the weakest part -
trailing into an intention instead of a status, skipping the closing ask, or grafting an
orphaned fragment onto a sentence that already ran out.

- _(none recorded yet)_

## What is working

Things that have measurably improved. Worth keeping visible: it is the only part of
this file that says *stop worrying about this one*.

- _(nothing recorded yet)_
