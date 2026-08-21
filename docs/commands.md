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
    press Transcribe       ->  transcribes, status stays recorded
    press "Ready for AI    ->  status pending  (PRD 6.3: the frontend flags, you pull -
     processing"                nothing reaches the queue without this explicit step)
/process-session           ->  feedback + score written, status processed
```

Transcribing and queueing are two separate, deliberate actions — the frontend's old
single "Process" button is now "Transcribe" followed by "Ready for AI processing" (they
can still be one click via `POST /process` with no `transcribe` param, which is what
"Process again" / "Re-queue" use). `pending` is only ever set once
`data/transcripts/<id>.json` exists, so every session in the queue has something for
Claude to read. A session whose transcription failed stays `recorded` and reports
`queued: false`; the failure is in `transcribe_error`.

Three variants share the pipeline but end differently:

- **`worklog`** — record under **Worklog**, transcribe, queue, then run `/log-work` — the
  result is a journal entry in `data/worklog/daily/`, not feedback or a score.
- **`brainstorm`** — record under **Brainstorm**, transcribe, queue, then run
  `/process-brainstorm` — the result is an ideas note in `data/brainstorm/`, not feedback
  or a score.
- **`journal`** — record under **Daily journal**, transcribe. That's it: the plain-text
  transcript *is* the entry, the session finalises itself the moment transcription
  finishes, and it is never queued for Claude at all - `enqueue`/`POST /process` refuses
  it outright. Nothing about this mode ever leaves the machine.

`worklog`, `brainstorm`, and `journal` (plus `freeform`) can be switched into or out of
after recording, before the session is `processed` — `PATCH /api/sessions/{id}/mode` /
`ect session set-mode <id> <mode>` — in case the wrong one got picked at record time.

---

## Skills

### `/generate-topic [recommended|interview|freeform] [category…]`

Creates one practice session. Reads `data/profile.md` and the words due for review,
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
`data/profile.md`.

Feedback always contains: filler and hesitation analysis (textual **and** acoustic),
stronger word/phrase/idiom swaps, sentence-structure rewrites, grammar corrections,
target-word usage check, a **model answer**, the rubric score, and one next focus.

### `/log-work [<session_id>]`

Turns pending `worklog` sessions (the daily spoken work journal) into structured
entries: projects, decisions with the why, hurdles, wins, competency tags from a
controlled list, plus a short title and one-line summary. Files the entry via
`ect worklog add`, which stores the markdown at `data/worklog/daily/<date>-<slug>.md`
(the title, slugified), indexes it for retrieval, and marks the session processed. A
second recording on the same date is merged, never overwritten blindly. No score —
worklog sessions are journal captures, not practice, and `/process-session` skips them.

### `/process-brainstorm [<session_id>]`

Turns pending `brainstorm` sessions (idea-dumps, no target words) into an organised
note: ideas, open threads, and next steps, plus a short title and one-line summary.
Files it via `ect brainstorm add`, which stores the markdown at
`data/brainstorm/<id>-<slug>.md` and marks the session processed. No score, no target
words, no vocabulary updates — same reasoning as worklog: this is extraction, not
practice, and `/process-session` skips it too.

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
uv run ect session enqueue 12                          # what the UI "Ready for AI" button does
uv run ect session pending
uv run ect session set-mode 12 worklog                 # freeform|worklog|brainstorm|journal only,
                                                         # and only before the session is processed
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
  "title": "Incident retro walkthrough",
  "summary": "Walked through the payment-migration incident retro",
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

- `title`/`summary`: optional but expected. `title` becomes part of the feedback
  filename (`data/feedback/<id>-<slug>.md`, slugified) and what the frontend's session
  list shows in place of the raw topic; `summary` is the one-line secondary text next to
  it. Omit either and the old behaviour applies (`data/feedback/<id>.md`, raw topic
  shown).
- `scores`: 0-10 each. **No `overall`** — it is computed. `target_usage` is dropped
  automatically for `freeform`.
- `target_words`: list **every** target word of the session. An omitted word is never
  rescheduled. `used_correctly` drives SM-2: correct use pushes the due date out,
  misuse (`quality 2`) or non-use (`quality 1`) brings it straight back. Pass an
  explicit `quality` (0-5) to override that mapping.
- `new_words`: professional/elevating terms only. `source` is `recommended` (you
  suggested it) or `user_speech` (they produced it and it is worth consolidating).
- `suggestions`: optional; populates the frontend's Suggestions page.

### Worklog

```bash
uv run ect worklog add --markdown entry.md --date 2026-08-14 \
    --summary "Shipped batched writes; cut p99 40%" \
    --title "Payment migration batched writes" \
    --projects "payment-migration,oncall" --tags "debugging,cross-team" --session 12

uv run ect worklog list --month 2026-08 --tag conflict --project payment-migration
uv run ect worklog show 2026-08-14        # daily entry
uv run ect worklog show 2026-08           # monthly rollup
uv run ect worklog rollup status          # completed months with entries but no rollup
uv run ect worklog rollup add --month 2026-08 --markdown rollup.md
```

`worklog add` is the journal's single write path: it validates the tags against the
controlled list (PRD-worklog 6.1), files the markdown at
`data/worklog/daily/<date>-<slug>.md` (the `--title`, slugified; one file per date — a
changed title on a same-day re-add removes the stale file rather than leaving an orphan),
upserts the index row, and if `--session` is given links the session, mirrors
`title`/`summary` onto it, and flips it to `processed`. `ect feedback apply` refuses
worklog sessions by design.

Because the filename carries a title, `ect worklog show <date>` no longer guesses the
path from the date — it looks up the index row and reads whatever `path` it points to.

Entries outlive sessions: deleting a worklog session from the frontend removes the
recording and transcript but never the journal entry.

### Brainstorm

```bash
uv run ect brainstorm add --session 12 --markdown ideas.md \
    --title "CLI tool for log triage" --summary "Sketched a CLI to triage prod logs by service"
```

The idea-dump write path — no date-merge, no tags, no rollups (each brainstorm session
is a standalone capture, not a slice of a continuous journal). Stores the markdown at
`data/brainstorm/<id>-<slug>.md`, mirrors `title`/`summary` onto the session, and flips
it to `processed`. `ect feedback apply` refuses brainstorm sessions too, for the same
reason it refuses worklog: there is no score to insert.

### Journal

No CLI write path — nothing to write. `ect session transcribe <id>` on a `journal`
session finalises it directly: the plain-text transcript sibling
(`data/transcripts/<id>.txt`) becomes the entry, `feedback_path` is pointed at it, and
`status` goes straight to `processed`. `ect session enqueue`/`POST /process` refuse
journal sessions outright — this mode is never queued for Claude, by design.

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

`ect session brief` is mode-aware: the annotated form above is for coached modes only
(`recommended`/`freeform`/`interview`). `worklog` and `brainstorm` get a much leaner
brief instead — a one-line header plus the plain transcript text, no measurements, no
target-word section — since neither is coached and the annotations would be pure token
cost with nothing for the skill to act on. That plain text comes from
`data/transcripts/<id>.txt`, a sibling the pipeline writes next to the JSON specifically
so a content-only skill never has to touch the word-timing array to get it. `journal`
never calls `brief` at all — see the Journal section above.

---

## Running the server

```bash
cd backend && uv run uvicorn app.main:app --reload --port 8000   # API + /docs
cd frontend && npm run dev                                       # http://localhost:5173
```

Useful endpoints: `GET /api/health`, `GET /api/doctor`, `GET /api/queue`,
`POST /api/sessions/{id}/transcribe` (transcribe only), `POST /api/sessions/{id}/process`
(`?transcribe=false` to queue an already-transcribed session without re-running the GPU
pipeline), `PATCH /api/sessions/{id}/mode`, `GET /api/sessions/{id}/brief`.
