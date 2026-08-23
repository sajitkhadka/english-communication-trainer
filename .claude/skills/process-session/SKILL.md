---
name: process-session
description: Analyse recorded English practice sessions - filler and hesitation analysis, vocabulary and sentence-structure improvements, target-word usage, a model answer, and a rubric score - then write feedback and update the vocabulary DB and user profile. Use when the user asks to process a session, wants feedback on a recording, or runs /process-session.
---

# process-session

Turn recorded sessions into feedback. The backend has already done every deterministic
thing: transcription, word timings, the pause map, filler counts, acoustic hesitations,
target-word matching. **Your job is linguistic judgement only** - never open the audio,
never recompute a number the brief already gives you.

All commands run from the `backend/` directory.

## Arguments

| Invocation | Behaviour |
|---|---|
| `/process-session` | process every session flagged `pending` |
| `/process-session 12` | process session 12 only; if already `processed`, stop and say "nothing to process" |
| `/process-session 12 --force` | reprocess session 12 even though it is already processed |

## 1. Find the work

```bash
cd backend
uv run ect session pending          # no argument
uv run ect session show 12          # specific id - check `status` before doing anything
```

If a specific session is already `processed` and `--force` was not passed, say so
plainly and stop. If nothing is pending, say that and stop.

**Skip sessions whose `mode` is `worklog`** - they are journal captures, not practice,
and belong to `/log-work`. If the queue contains only worklog sessions, say so and
point the user at `/log-work` instead.

## 2. Make sure it is transcribed

If `has_transcript` is false (or `transcribe_status` is not `done`):

```bash
uv run ect session transcribe 12
```

This is GPU work and takes seconds to a minute. If it fails, report the error and move
on to the next session rather than abandoning the whole run.

## 3. Read the brief, not the raw JSON

```bash
uv run ect session brief 12
```

The brief is numbered sentences with every backend measurement attached to the sentence
it happened in. Cite sentences as `S7`. `data/transcripts/12.json` also exists but is
mostly word-level timestamps for the frontend's subtitle sync - reading it wastes
tokens for no extra insight.

Also read `docs/rubric.md` before scoring, every time. It holds the anchors that keep
scores comparable across sessions.

Then two cheap reads that make this session's feedback build on the previous ones
instead of starting over:

```bash
uv run ect vocab gaps --limit 25
```

`vocab gaps` splits the corpus by whether the user actually *produces* each term:
`dormant` (carried as a target, still never used correctly), `shaky`, `untried`, plus
an `activation_rate` overall and per `kind`. The per-kind rates are usually where the
finding is - idioms and phrases activate far more slowly than single words. It is a
corpus-wide measurement, not a per-session one; section 4 below is where it turns into
advice.

Read `data/learning-notes.md` as well (repo root, not `backend/data/` - commands run
from `backend/`). It is the durable record of what has already been coached. Do not
spend a section restating a lesson that is already in there and already landing - note
in one line that it is holding, and spend the words on something new.

## 4. Write the feedback

Write the markdown to a **scratch file** (anywhere - a temp path is fine) and hand it to
the backend with `--markdown` in step 6. The backend owns the canonical location and
copies it to `data/feedback/<id>.md` itself.

Do **not** write into `data/` yourself. Commands here run from `backend/`, so a relative
`data/feedback/<id>.md` lands in `backend/data/` - the frontend reads the repo-root path,
finds nothing, and shows "No feedback yet" on a session that says `processed`.

The file has these sections, in order:

1. **Snapshot** - two or three lines: duration, wpm, filler rate, what stood out.
2. **Fillers & hesitation** - work from both layers. Textual fillers are in the
   transcript; acoustic hesitations are voiced sounds Whisper deleted, so they are real
   even though no word appears. Terms marked `?` in the brief are context-dependent -
   decide per instance whether that `like` or `actually` was filler or content, and say
   which. Point at the pattern (e.g. "hesitation clusters before every technical noun"),
   not just the count.
3. **Stronger word and phrase choices** - a table: what they said -> what a senior
   engineer would say -> why. Only professional, elevating swaps. Never suggest a word
   that is merely a synonym of equal register.
4. **Vocabulary you already have but didn't reach for** - the active-vocabulary
   section, and the counterpart to section 3: that one improves what they *said*, this
   one names what they *could have* said. Open with the one number worth repeating
   from `ect vocab gaps` (the overall `activation_rate`, and the weakest `by_kind`
   entry). Then pick **2-4** `dormant` or `untried` terms that would genuinely have
   fitted this session's content, and show exactly where - "at `S6` you said 'it was
   hard to know which one to pick'; that is where `weigh the trade-off` lives". Prefer
   phrases and idioms over single words: the per-kind rates almost always show those
   are the ones sitting passive. Skip a term rather than manufacture a slot for it - a
   phrase wedged into content it does not fit teaches the wrong thing.

   **Do not add these terms to `target_words` in the payload.** They were not this
   session's targets, so the user had no prompt to use them, and an SM-2 review off a
   word they were never asked for corrupts the ease factor that makes mastery mean
   anything. A dormant word comes back around through `/generate-topic`, which reads
   the same report and prefers them when choosing the next session's targets.
5. **Sentence structure** - concrete rewrites of the weakest 2-4 sentences, cited by
   `S<n>`. Name the problem (run-on, dropped subject, stacked prepositions, buried
   verb) so it is learnable. Where one of the frames in `data/learning-notes.md`
   would have fixed the sentence, name it - a rewrite the user can reuse beats a
   rewrite of this one sentence.
6. **Grammar** - only real errors: tense, agreement, articles, prepositions, plurals.
   Quote the sentence, give the correction. Do not invent errors to fill the section;
   if the grammar was clean, say so in one line.
7. **Target-word usage** - skip entirely for `freeform`. For each target word: used or
   not, and if used, whether it was used *correctly and naturally*. A word that
   appeared but was shoehorned in counts as misused - give a corrective example
   sentence in their own context.
8. **Model answer** - the most actionable part of the whole file. A polished rewrite of
   **what the user actually said**: same content, same experiences, same claims, at the
   register they are aiming for. Do not invent achievements or facts they did not
   mention. Keep it speakable - roughly the same length as the original, natural out
   loud, not written-essay prose. Naturally include the target words they missed.
9. **Score** - the table from step 5 below, with a one-line justification per dimension.
10. **Next focus** - exactly one thing to fix in the next session.

Also pick a short, filename-safe **title** (a few words, specific to what the session
was actually about - "Incident retro walkthrough", not "Session 12") and a one-line
**summary**. Both go in the JSON payload (step 6), not the markdown file itself - the
backend stores them on the session and uses the title to name the feedback file, and the
frontend's session list shows them in place of the raw topic so it stays scannable
without opening every session.

## 5. Score against the rubric

Score 0-10 per dimension using `docs/rubric.md`. Omit `target_usage` for `freeform`
sessions. **Do not compute `overall`** - the backend computes the weighted mean so the
number stays consistent across sessions.

## 6. Write it back

Build one JSON payload and apply it. This is the only path that touches the DB, and it
does all the arithmetic: weighted overall, SM-2 ease/interval/due dates, mastery.

```json
{
  "session_id": 12,
  "title": "Incident retro walkthrough",
  "summary": "Walked through the payment-migration incident retro, leaned on 'mitigate' and 'leverage'",
  "scores": {
    "vocab_range": 6.5, "filler_density": 4.0, "fluency": 6.0,
    "grammar": 7.5, "structure": 6.0, "coherence": 7.0, "target_usage": 5.0
  },
  "target_words": [
    {"term": "bottleneck", "used": true,  "used_correctly": true},
    {"term": "leverage",   "used": true,  "used_correctly": false,
     "note": "used as a synonym for 'use'; needs an object worth leveraging"},
    {"term": "mitigate",   "used": false, "used_correctly": false}
  ],
  "new_words": [
    {"term": "de-risk", "kind": "word", "meaning": "reduce the risk of a plan or change",
     "example": "We de-risked the migration by shadowing traffic for a week.",
     "source": "recommended"}
  ],
  "suggestions": [
    {"mode": "recommended", "category": "incident review",
     "topic": "...", "target_words": ["de-risk"], "rationale": "..."}
  ]
}
```

```bash
uv run ect feedback apply --json /path/to/payload.json --markdown /path/to/feedback.md
```

Rules for the payload:

- `target_words` must list **every** target word of the session, used or not. Omitting a
  word means it is never rescheduled. `used_correctly` drives SM-2: correct use pushes
  the due date out, misuse or non-use brings it back fast.
- `new_words`: only professional or elevating terms - vocabulary that raises the
  register. Two to five per session is healthy. **Never add basic words.** Two sources
  qualify: terms you recommended in section 3, and strong terms the user produced
  themselves that are worth consolidating (`"source": "user_speech"`).
- If the user under-used or misused a word that is already in the corpus, add it to
  `target_words` with a `note` rather than to `new_words` - that is what resurfaces it.
- The dormant terms named in section 4 belong in **neither** list. They are already in
  the corpus, and they were never this session's targets - see section 4.
- `suggestions` is optional; it populates the frontend's Suggestions page.

This call stores the markdown at `data/feedback/<id>-<slug>.md` (using the `title` from
the payload; falls back to `data/feedback/<id>.md` if `title` is omitted), links it to
the session and flips the status to `processed`. It refuses to run without markdown - if
it reports "no feedback markdown for session <id>", the `--markdown` path was wrong; fix
it and re-run rather than applying the payload alone.

## 7. Update the profile (every run, no separate command)

Read `data/profile.md` (repo root, not `backend/data/` - commands run from `backend/`).
It is gitignored personal state seeded from `docs/profile.example.md`; edit the live file,
never the template. If the session revealed anything genuinely new and durable
about the user - role, employer, team, tech stack, current projects, interests,
recurring language weaknesses - fold it into the right section with a small edit.

- Additive only. Never rewrite or delete existing facts.
- Only durable facts. "Worked on the payment service" belongs there; "was tired today"
  does not.
- Recurring language weaknesses go under *Language patterns to work on*: that section
  is what makes future topics target real gaps.
- If nothing new came up, leave the file alone and say so.

## 8. Update the learning notes (every run)

`data/learning-notes.md` is the durable half of the loop: `data/feedback/<id>.md` is
per-session and never reread, this file is what carries forward. Same shape as step 7 -
read it, make a small edit - with one difference: it is **consolidated, not
append-only**.

- A lesson goes in only once it has shown up **twice**. One occurrence is an incident;
  this file is for patterns.
- A term named in section 4 goes under *Phrases and connectors to activate*, and comes
  off that list the session it shows up unprompted.
- Record which sentence patterns are landing under *Sentence patterns*, from what the
  recordings actually show rather than from what the file already lists.
- Move anything genuinely fixed to *What is working* instead of leaving it standing as
  a warning, and merge duplicates rather than stacking them.
- Prune. A file that only grows stops being read, which defeats the point of having it.
- Nothing new and nothing to consolidate is a normal outcome - leave it alone and say so.

Keep it distinct from the profile: *how they speak* goes here, *who they are* goes in
`data/profile.md`. A recurring weakness is the one thing that belongs in both - the
profile's one-line version drives future topic selection, the detail and its fix live
here.

## 9. Report back

Per session, briefly: id, title, overall score and its move versus the previous session,
the one-line next focus, and how many words were rescheduled or added. Then the path to
the feedback file. Keep the console summary short - the detail lives in the markdown and
the frontend renders it.
