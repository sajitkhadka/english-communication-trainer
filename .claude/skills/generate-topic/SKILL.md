---
name: generate-topic
description: Generate a practice topic for the English Communication Trainer - a recommended topic with brand-new target vocabulary, an interview question, or a free-form prompt - personalised from data/profile.md and the words currently due for review. Use when the user asks for a new topic, a practice prompt, an interview question, or runs /generate-topic.
---

# generate-topic

Create one practice session for the user to record against. You choose the topic and
the target vocabulary; the backend owns the database writes.

All commands run from the `backend/` directory.

## 1. Gather context (three cheap reads)

```bash
cd backend
uv run ect vocab due --limit 15     # words needing review + corpus stats
uv run ect requests list            # topic requests raised from the frontend
```

Then read `data/profile.md` in full (repo root, not `backend/data/` - commands run from
`backend/`). It is short and it is the whole point: the topic
must connect to this user's actual role, stack, employer, and interests. If the profile
is still a template with no real facts in it, say so in your final message and pick a
topic that suits a generic mid-level software engineer.

## 2. Decide the mode

Parse the user's arguments loosely:

| They said | Mode | Notes |
|---|---|---|
| nothing, or `recommended` | `recommended` | topic + target words |
| `interview`, or a question-shaped request | `interview` | one question, **no target words** |
| `freeform` | `freeform` | prompt only, no target words |

Anything left over is the `--category` (e.g. `interview system-design` -> mode
`interview`, category `system-design`). If an open request from `ect requests list`
matches what they asked for, use its mode and category and close it in step 5.

## 3. Choose target vocabulary (recommended mode only)

Pick **5-6 terms**, blended (PRD 10):

- **3-4 due words** from `ect vocab due`. Prefer the lowest `mastery`, and always
  include anything with a `notes` field describing a past misuse - that is the word the
  user actually needs another go at.
- **2-3 brand-new terms** not in the corpus: professional, elevating vocabulary a
  senior engineer would use in a design review, a stakeholder update, or an interview.
  Idioms and multi-word phrases are good - vary `kind` across `word`/`phrase`/`idiom`.

Do not pick basic vocabulary. If a competent B2 speaker already uses it daily, it does
not belong here.

New terms need a `meaning` and a natural `example`, so register them before creating
the session:

```bash
uv run ect vocab add --json '[{"term":"...","kind":"phrase","meaning":"...","example":"...","source":"recommended"}]'
```

## 4. Write the topic

One or two sentences the user can speak to for 2-4 minutes. It should:

- draw on something concrete from the profile rather than a generic prompt;
- invite structure (a decision, a trade-off, an outcome), because coherence and
  sentence structure are scored;
- for `interview` mode, be exactly one question, phrased the way an interviewer would
  actually ask it - and set no target words.

## 5. Create the session

```bash
uv run ect session create --mode recommended \
  --category "system-design" \
  --topic "..." \
  --target-words "leverage,bottleneck,de-risk"
```

This writes the `sessions` row (status `awaiting_recording`) and
`data/prompts/<id>.json`, which is what the frontend renders. If you consumed a
request: `uv run ect requests close <id>`.

## 6. Report back

Show the user, in plain text:

- the session id, and that the frontend will now show it under the matching section;
- the topic;
- each target word with its kind and meaning, marking which are review words and which
  are new;
- one line on why this topic (which profile fact it draws on).

Do not analyse anything. Recording comes next, then `/process-session`.
