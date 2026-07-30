# Scoring Rubric

Read this file before scoring, every time. Scores are only useful if they mean the same
thing in session 40 as they did in session 4, and the anchors below are what keep them
stable.

Each dimension is scored **0-10**. `overall` is a weighted mean **computed by the
backend**, not by the model - do not put an `overall` key in the payload.

| Dimension | Weight |
|---|---|
| Vocabulary range | 0.18 |
| Filler density | 0.15 |
| Fluency / pace | 0.15 |
| Grammar | 0.15 |
| Coherence | 0.15 |
| Sentence structure | 0.14 |
| Target-word usage | 0.08 |

For `freeform` sessions, omit `target_usage`; the backend renormalises the remaining
weights so the number stays comparable.

---

## How to score

Judge the answer against **a competent senior engineer speaking to colleagues or an
interviewer** - not against a native-speaker essayist, and not against the user's
previous best. Anchors are targets, not curves. A steady 6.5 that becomes a steady 7.5
over two months is exactly what this tool is for; inflating a 6 to a 7 because it was
"better than last time" destroys that signal.

Half points are fine. Reserve 9-10 for genuinely excellent, and be willing to give the
same score twice in a row.

---

## Vocabulary range

Variety and register of professional terms; precision of word choice; use of idiom that
lands naturally.

- **9-10** — Precise, varied, senior register. Idiom used naturally. No word feels reached for.
- **7-8** — Clearly professional. Some strong collocations. A few generic choices (`good`, `a lot of`, `stuff`) remain.
- **5-6** — Understandable and mostly correct, but plain. Leans on general-purpose words where a precise technical one exists.
- **3-4** — Repetitive core vocabulary. Vague nouns (`thing`, `part`, `issue`) carry most of the meaning.
- **0-2** — Vocabulary actively obscures the content.

## Filler density

Use the **combined** rate from the brief: textual fillers plus acoustic hesitations per
minute. The acoustic layer counts even though those sounds are missing from the
transcript - that is the whole point of measuring it separately (PRD 5.2).

Decide per instance whether a `?`-flagged term (`like`, `so`, `well`, `actually`,
`basically`) was filler or legitimate content, and score only the filler uses.

- **9-10** — Under 1/min. Effectively none.
- **7-8** — 1-3/min. Present but unobtrusive.
- **5-6** — 3-6/min. Noticeable; a listener starts tracking them.
- **3-4** — 6-10/min. Distracting; undercuts authority.
- **0-2** — Over 10/min. Hard to follow the content through the noise.

## Fluency / pace

WPM in band, pause distribution, rhythm. Target band is **130-160 wpm** for
professional speech; 110-130 is acceptable if delivery is deliberate rather than
struggling.

Weigh *where* pauses fall more heavily than how many there are. Mid-sentence pauses
(the brief marks them) signal word-searching; between-sentence pauses are normal and
often good.

- **9-10** — In band, even rhythm, pauses fall at clause boundaries and read as intentional.
- **7-8** — In band with a few mid-sentence hesitations, or slightly slow but steady.
- **5-6** — Frequent mid-sentence stalls, or noticeably rushed/slow. Rhythm is uneven but followable.
- **3-4** — Long pauses (>1.5s) recur mid-thought; listener has to wait.
- **0-2** — Halting throughout.

## Grammar

Accuracy of tense, agreement, articles, prepositions, plurals, question forms.

Count *errors*, not accent or informality. Contractions, dropped auxiliaries in casual
speech, and self-corrections are normal spoken English, not mistakes.

- **9-10** — Error-free or a single slip that a native speaker would also make.
- **7-8** — Two or three minor errors (an article, a preposition) that do not impede meaning.
- **5-6** — Recurring pattern errors - a consistently wrong tense, systematically dropped articles.
- **3-4** — Errors in most sentences; the listener works to reconstruct the intent.
- **0-2** — Grammar blocks comprehension.

## Sentence structure

Variety of construction, clause control, avoidance of run-ons and fragments, and
whether the main verb arrives before the listener gives up.

- **9-10** — Deliberate variety: short punchy sentences next to well-controlled complex ones.
- **7-8** — Generally well-formed, some sameness of shape, an occasional run-on.
- **5-6** — Long chains joined by `and`/`so`/`because`. Subordination attempted unevenly.
- **3-4** — Mostly run-ons or fragments; ideas collide inside one sentence.
- **0-2** — No reliable sentence boundaries.

## Coherence

Logical flow and organisation. Does the answer have a shape - context, problem, action,
result - or is it a pile of related facts? Does it actually answer the question asked?

- **9-10** — Clear arc, explicit signposting, strong close. Answers exactly what was asked.
- **7-8** — Followable structure; the ending may trail or the context may be thin.
- **5-6** — Recognisable points in a loose order. Some backtracking or repetition.
- **3-4** — Jumps between ideas; the listener assembles the structure themselves.
- **0-2** — No discernible organisation, or does not answer the question.

## Target-word usage

`recommended` and `interview` only. Weigh *correct and natural* far above *present*: a
word wedged in to satisfy the brief is worth less than one used well.

- **9-10** — All target words used, correctly, and they sound like the user's own vocabulary.
- **7-8** — Most used correctly; one missing or slightly forced.
- **5-6** — Half used; or most used but visibly shoehorned.
- **3-4** — One or two used, at least one misused.
- **0-2** — None used, or all misused.

If a target word is misused, that belongs in the feedback as a corrective example **and**
in the payload as `used: true, used_correctly: false` with a `note` - that is what
brings the word back around quickly (PRD 10).

---

## The model answer

Every session's feedback carries a model answer, and it is the part the user will
actually reread. It is a **rewrite of what they said**, not a better answer to the
question.

- Same content, experiences, and claims. **Never invent an achievement, a metric, or a
  project they did not mention.**
- Their register raised to where they are aiming, not to written prose.
- Roughly the same length as the original. It has to be speakable out loud.
- Naturally absorb the target words they missed - if a word cannot be placed naturally
  in their own content, leave it out and say why in the target-word section.
