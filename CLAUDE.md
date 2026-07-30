# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

All backend commands run from `backend/`. `uv run ect …` and `uv run python -m app.cli …`
are equivalent — the second works even if the console script has not been regenerated.

```bash
# setup / diagnostics
uv sync --extra dev
uv run ect db init                   # schema + data/ tree, idempotent
uv run ect doctor                    # CUDA, VRAM, ctranslate2, ffmpeg, db. Exit 1 if unusable.

# tests
uv run pytest -q                     # whole suite, no GPU or model download needed
uv run pytest tests/test_metrics.py -q
uv run pytest tests/test_workflow.py::TestRecordFeedback::test_new_words_are_added -q
uv run pytest -q -k "hesitation or srs"

# lint
uv run ruff check .
uv run ruff format .

# servers
uv run uvicorn app.main:app --reload --port 8000     # API + /docs
cd ../frontend && npm run dev                        # :5173, proxies /api to :8000
```

Frontend (`frontend/`): `npm run typecheck` (strict, `noUnusedLocals`), `npm run build`
(runs `tsc -b` then Vite). There is no frontend test suite.

`ect doctor` first when anything GPU-shaped misbehaves — it reports `vram_free_gb` and
the registered DLL directories, which is usually the whole answer.

## The architecture in one idea

**The backend does everything deterministic; Claude does judgement only.** This is not a
style preference — it is what keeps token cost low, feedback quality high, and scores
comparable across sessions. Every change should preserve it.

Concretely, the following are computed in Python and must **never** move into a skill or
a prompt:

- the weighted `overall` score (`app/db.py:compute_overall`, weights in `SCORE_WEIGHTS`)
- SM-2 ease, interval, due date, mastery (`app/srs.py`)
- WPM, pause map, filler counts, acoustic hesitations, target-word matching (`pipeline/metrics.py`)

Claude supplies per-dimension scores 0-10 and a `used` / `used_correctly` verdict per
target word. That is the entire judgement surface.

### Three seams that matter

**1. `ect` CLI is the only interface skills use.** No skill contains SQL or writes to the
DB directly. Adding capability for a skill means adding an `ect` subcommand in
`app/cli.py`, which delegates to `app/services.py`. The CLI works with the server
stopped; the HTTP API in `app/routers/` calls the same service functions so both paths
can never drift.

**2. `ect session brief <id>` is what Claude reads, not `data/transcripts/<id>.json`.**
The JSON is complete and carries per-word timings for the frontend's subtitle sync —
mostly size, no linguistic value. `app/brief.py` renders numbered sentences with each
measurement attached to the sentence it occurred in (`S3.` … `! fillers: … | pause 0.3s
… | hesitation 0.4s (untranscribed)`), so the model can cite `S3` and a five-minute
answer costs hundreds of tokens instead of thousands. If you add a measurement to the
pipeline, add it to the brief or the model will never see it.

**3. `services.record_feedback` is the single write path for analysis.** One JSON payload
applies the score, SM-2 updates for every target word, new vocabulary, suggestions, and
the status flip. It validates dimension names and ranges and rejects an `overall` key.
Writing `data/feedback/<id>.md` before the call is what links the file to the session.

### Two independent audio layers

`pipeline/transcribe.py` (WhisperX + wav2vec2 forced alignment) and `pipeline/vad.py`
(Silero) deliberately do not know about each other. Whisper normalises "um"/"uh" out of
transcripts; Silero still hears them. `metrics.py:find_hesitations` reports any voiced
span longer than `min_hesitation_sec` whose overlap with aligned words is under
`hesitation_max_word_coverage` as an untranscribed hesitation. **Do not "simplify" this
by reusing WhisperX's internal pyannote VAD** — that couples the auditor to the thing it
audits. See `docs/adr/0002-whisperx-plus-silero-vad.md`.

`pipeline/metrics.py` takes plain `Word` and `Span` values and returns dataclasses. It
never touches audio, which is exactly why the analysis is unit-testable without a GPU.
Keep it that way when adding metrics.

### Two status fields, not one

`sessions.status` tracks the **user/Claude workflow** (`awaiting_recording` → `recorded`
→ `pending` → `processed`). `sessions.transcribe_status` tracks the **GPU stage**
(`none`/`running`/`done`/`error`) because transcription runs independently of the Claude
handoff. Do not collapse them.

The frontend never calls Claude. Pressing Process sets `pending` and transcribes; the
user runs `/process-session` themselves (`docs/adr/0003-…`). Any UI text about processing
must stay honest about that — `ProcessResponse.hint` carries the wording.

## Conventions worth knowing before editing

- **`settings` is an `lru_cache` singleton** (`app/config.py`). Tests redirect storage
  with `monkeypatch.setattr(settings, "data_dir", …)`, not env vars — see
  `tests/conftest.py`. Every path in the app derives from `settings`, so nothing else
  needs to change.
- **Paths are stored relative to the repo root** via `paths.relpath` / `abspath`, so the
  DB stays portable. Do not store absolute paths.
- **Audio decoding goes through ffmpeg** (`pipeline/audio.py`), never torchaudio — the
  browser uploads webm/opus or mp4, and the installed torchcodec backend does not work
  on Windows. A `torchcodec` import warning is expected and harmless.
- **Models are cached in module-level dicts** in `transcribe.py` and loaded once per
  process; 6 GB of VRAM cannot afford a reload per request. `load_asr` walks a
  compute-type fallback ladder and records what it actually used in
  `transcript.json:meta.compute_type`.
- **`schema.sql` is applied with `CREATE TABLE IF NOT EXISTS`** and re-run on every CLI
  invocation. It is not a migration system: adding a column to an existing DB needs an
  explicit `ALTER TABLE`. Columns beyond PRD §7.2 are marked `ADDITIVE`.
- **Skills live in `.claude/skills/<name>/SKILL.md`**, not `skills/` as the PRD sketches.
  Claude Code only discovers them there.
- **Frontend colors come from CSS custom properties** in `src/styles.css` (a
  CVD-validated palette with separately chosen light and dark steps). Components
  reference `var(--series-1)` etc. — do not hard-code hex in a component.

## Adding a score dimension

It touches six places, and missing one produces a silently wrong `overall`:

1. `backend/app/schema.sql` — column on `scores`
2. `backend/app/db.py` — `SCORE_DIMENSIONS` **and** `SCORE_WEIGHTS` (weights must sum to 1.0)
3. `backend/app/db.py:insert_score` — it unpacks `SCORE_DIMENSIONS` positionally
4. `docs/rubric.md` — anchors, or scores drift between sessions
5. `.claude/skills/process-session/SKILL.md` — the example payload
6. `frontend/src/components/common.tsx` — `DIMENSIONS`, and `types.ts:Score`

`target_usage` is the model for a dimension that does not always apply: it is dropped for
`freeform` sessions and the remaining weights renormalise.

## Reference

- `PRD.md` — the specification this implements
- `docs/commands.md` — every skill and `ect` command, with payload shapes
- `docs/rubric.md` — scoring anchors; read before scoring anything
- `docs/setup.md` — prerequisites, VRAM ladder, config env vars, troubleshooting
- `docs/adr/` — the three load-bearing decisions and the alternatives rejected
