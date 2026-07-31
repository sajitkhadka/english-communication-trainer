# Command Reference

Two surfaces, one backend:

- **`/skills` in the Claude Code console** — what you use day to day.
- **`ect` CLI** — the deterministic command surface those skills drive. Everything works
  with the FastAPI server stopped; only the frontend needs it running.

All `ect` commands run from `backend/`. `uv run ect …` and
`uv run python -m app.cli …` are equivalent.

---

## The loop

```
/generate-topic            ->  session created, status awaiting_recording
    record in the frontend ->  status recorded
    press Process          ->  transcribes, then status pending if the transcript
                               landed  (PRD 6.3: the frontend flags, you pull)
/process-session           ->  feedback + score written, status processed
```

`pending` is only ever set once `data/transcripts/<id>.json` exists, so every session in
the queue has something for Claude to read. A session whose transcription failed stays
`recorded` and reports `queued: false`; the failure is in `transcribe_error`.

---

## Skills

### `/generate-topic [recommended|interview|freeform] [category…]`

Creates one practice session. Reads `docs/profile.md` and the words due for review,
picks 5-6 target words (a blend of due and brand-new) for `recommended`, writes the
session row and `data/prompts/<id>.json`.

```
/generate-topic
/generate-topic interview system-design
/generate-topic freeform
```

`interview` and `freeform` sessions carry no target words.

### `/process-session [<session_id>] [--force]`

| Invocation | Behaviour |
|---|---|
| `/process-session` | processes every session flagged `pending` |
| `/process-session 12` | session 12 only; no-ops with a clear message if already processed |
| `/process-session 12 --force` | reprocesses an already-processed session |

Writes `data/feedback/<id>.md`, inserts the score, applies SM-2 updates to every target
word, adds new vocabulary, and folds anything newly learned about you into
`docs/profile.md`.

Feedback always contains: filler and hesitation analysis (textual **and** acoustic),
stronger word/phrase/idiom swaps, sentence-structure rewrites, grammar corrections,
target-word usage check, a **model answer**, the rubric score, and one next focus.

### `/vocab-review [limit]`

Read-only view of the vocabulary corpus: what is due, what is weakest, what has never
been practised, and corpus stats. Never modifies scheduling — that only ever comes from
`/process-session`.

---

## `ect` CLI

### Setup & diagnostics

```bash
uv run ect db init      # create the schema + data/ tree (idempotent, safe to re-run)
uv run ect doctor       # CUDA, VRAM, ctranslate2, ffmpeg, db, profile. Exit 1 if unusable.
```

### Vocabulary

```bash
uv run ect vocab due --limit 15
uv run ect vocab list --sort mastery --limit 30    # recency|frequency|mastery|alpha|due
uv run ect vocab stats
uv run ect vocab add --json '[{"term":"de-risk","kind":"word","meaning":"…","example":"…"}]'
```

`--json` accepts a file path, `-` for stdin, or a literal JSON string.

### Sessions

```bash
uv run ect session create --mode recommended --category "system-design" \
    --topic "…" --target-words "leverage,bottleneck,de-risk"

uv run ect session list --mode interview --status processed
uv run ect session show 12
uv run ect session brief 12 [--max-sentences 40]
uv run ect session transcribe 12 [--force]
uv run ect session attach 12 --audio ~/voice-memo.m4a   # recorded outside the browser
uv run ect session enqueue 12                          # what the UI Process button does
uv run ect session pending
```

### Feedback write-back

```bash
uv run ect feedback apply --json payload.json --markdown path/to/feedback.md
```

Applies one session's analysis. Everything numeric happens here, never in the model:
the weighted `overall`, SM-2 ease/interval/due dates, and derived mastery. The markdown
is copied to `data/feedback/<id>.md` — the canonical path is the backend's to decide, so
`--markdown` can point anywhere. That is what links the file to the session and flips the
status to `processed`.

`--markdown` may only be omitted when `data/feedback/<id>.md` already exists (a re-apply);
otherwise the command errors rather than marking a session `processed` with no feedback
for the frontend to render.

Payload:

```json
{
  "session_id": 12,
  "scores": {"vocab_range": 6.5, "filler_density": 4.0, "fluency": 6.0,
             "grammar": 7.5, "structure": 6.0, "coherence": 7.0, "target_usage": 5.0},
  "target_words": [
    {"term": "bottleneck", "used": true, "used_correctly": true},
    {"term": "leverage", "used": true, "used_correctly": false, "note": "used as a plain synonym for 'use'"},
    {"term": "mitigate", "used": false, "used_correctly": false}
  ],
  "new_words": [{"term": "de-risk", "kind": "word", "meaning": "…", "example": "…", "source": "recommended"}],
  "suggestions": [{"mode": "recommended", "category": "incident review", "topic": "…",
                   "target_words": ["de-risk"], "rationale": "…"}]
}
```

- `scores`: 0-10 each. **No `overall`** — it is computed. `target_usage` is dropped
  automatically for `freeform`.
- `target_words`: list **every** target word of the session. An omitted word is never
  rescheduled. `used_correctly` drives SM-2: correct use pushes the due date out,
  misuse (`quality 2`) or non-use (`quality 1`) brings it straight back. Pass an
  explicit `quality` (0-5) to override that mapping.
- `new_words`: professional/elevating terms only. `source` is `recommended` (you
  suggested it) or `user_speech` (they produced it and it is worth consolidating).
- `suggestions`: optional; populates the frontend's Suggestions page.

### Suggestions

```bash
uv run ect requests list          # topic requests raised from the frontend
uv run ect requests close 3
uv run ect suggest add --json '[{"mode":"interview","category":"behavioural","topic":"…"}]'
```

---

## Why `brief` and not the raw transcript

`data/transcripts/<id>.json` is the complete artefact and includes per-word timestamps,
which is most of its size and exists for the frontend's subtitle sync.
`ect session brief` renders the same analysis as numbered sentences with every
measurement attached to the sentence it happened in:

```
S3. [192.5 wpm] I basically, you know, decided to leverage a queue to smooth out the load.
    ! fillers: basically?, you know | pause 0.3s after "basically," mid-sentence | hesitation 0.4s after "know," (untranscribed)
```

A `?` marks a context-dependent filler for the model to judge. `(untranscribed)` marks
an acoustic hesitation — a sound Silero heard that Whisper deleted (PRD 5.2). Sentences
are citable as `S3`, and a five-minute answer costs a few hundred tokens instead of
several thousand.

---

## Running the server

```bash
cd backend && uv run uvicorn app.main:app --reload --port 8000   # API + /docs
cd frontend && npm run dev                                       # http://localhost:5173
```

Useful endpoints: `GET /api/health`, `GET /api/doctor`, `GET /api/queue`,
`POST /api/sessions/{id}/process`, `GET /api/sessions/{id}/brief`.
