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
./dev.ps1                                            # both, from the repo root (Ctrl+C stops both)
uv run uvicorn app.main:app --reload --port 8000     # API + /docs, on its own
cd ../frontend && npm run dev                        # :5173, proxies /api to :8000
```

`dev.ps1` refuses to start if the API port is already served and warns if 5173 is — a
stale server from an earlier run is otherwise invisible until the UI shows stale data.
It passes `ECT_API_PORT` to Vite, so `-Port` moves both halves together.

Frontend (`frontend/`): `npm run typecheck` (strict, `noUnusedLocals`), `npm run build`
(runs `tsc -b` then Vite). There is no frontend test suite.

```bash
# remote capture (ADR 0006) - only if the relay is deployed
uv run ect agent status              # can it reach the relay and the local API?
uv run ect agent once                # one drain + digest pass; what to run when a
                                     # phone recording "did not arrive"
cd ../relay && go test ./...         # the relay's own suite: no cluster, no GPU
```

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

`brief_for_session` is mode-aware: that annotated form is for coached modes only. For
`worklog`/`brainstorm` (`brief.CONTENT_ONLY_MODES`) it renders a much leaner form
instead — a one-line header plus plain text, read from `data/transcripts/<id>.txt`
(a sibling `pipeline/runner.py` writes next to the JSON) rather than the annotated
sentences, since neither mode is coached and the measurements are pure token cost with
nothing for the skill to act on. `journal` never calls `brief_for_session` at all — see
below.

**3. `services.record_feedback` is the single write path for analysis.** One JSON payload
applies the score, SM-2 updates for every target word, new vocabulary, suggestions, and
the status flip. It validates dimension names and ranges and rejects an `overall` key.
Writing `data/feedback/<id>.md` before the call is what links the file to the session.
It refuses `worklog` and `brainstorm` sessions outright — scoring a journal or an idea
dump rewards performing over reporting (PRD-worklog 5.1), so each has its own write path
(`record_worklog_entry` / `record_brainstorm_entry`) that skips `insert_score` entirely
rather than inserting a meaningless row. `journal` never reaches any write path — see
below.

**4. Remote capture goes through the relay's inbox, never straight into the DB.**
`ect agent` (`app/agent.py`) drains recordings captured on a phone by driving the
**local HTTP API** - `POST /api/sessions`, `/recording`, `/transcribe` - and never
imports `services`. That is a correctness requirement, not layering taste:
`services._gpu_lock` is a `threading.Lock`, so it only guards one process, and two
processes each loading `large-v3` do not fit in 6 GB and take the worker down natively.
Driving the API keeps every WhisperX load inside the one uvicorn process where the lock
means something. The agent needs no torch, no CUDA and no `pipeline` import at all.

`sessions.external_uid` is what makes that safe to repeat: the *recorder* mints the id,
so a retried upload and a re-drained item both collapse into the same session
(`create_session` returns the existing row). A drained recording stops at `recorded` -
ADR 0003 stands, and arriving over the network does not change it. `journal` is the free
exception, because it finalises itself at transcription.

The whole thing is optional: with `ECT_RELAY_URL` unset, nothing above runs and the app
is exactly what it was. See `docs/relay.md` and `docs/adr/0006-…`.

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

The frontend never calls Claude. Transcribing and queueing are two separate, explicit
user actions — "Transcribe" (`POST /transcribe`, leaves `status` at `recorded`) and
"Ready for AI processing" (`POST /process?transcribe=false`, the only thing that sets
`pending`) — so nothing reaches the queue by accident. `services.enqueue` still accepts
one combined call (`transcribe` left unset) for "Process again"/"Re-queue", but only a
session with a transcript is ever queueable, so the queue never advertises work Claude
cannot do; the user runs `/process-session` (or `/log-work`, `/process-brainstorm`, or
`/process-queue`, which routes a mixed queue to those three) themselves
(`docs/adr/0003-…`). `services.transcribe_session` holds a process-wide lock:
two concurrent WhisperX loads do not fit in 6 GB and take the whole worker down
natively, which leaves `transcribe_status` stuck at `running` with no exception to
record. Any UI text about processing must stay honest about that — `ProcessResponse.hint`
carries the wording.

`journal` sessions take a third path that isn't either of the above: `_transcribe_session`
finalises them directly to `processed` the moment transcription finishes (the plain-text
transcript *is* the entry - see `data/transcripts/<id>.txt` in seam #2 above), and
`enqueue` refuses them outright as a backend invariant, not a UI omission — this mode
must never reach Claude, by design.

## Conventions worth knowing before editing

- **`settings` is an `lru_cache` singleton** (`app/config.py`). Tests redirect storage
  with `monkeypatch.setattr(settings, "data_dir", …)`, not env vars — see
  `tests/conftest.py`. Every path in the app derives from `settings`, so nothing else
  needs to change.
- **Paths are stored relative to the repo root** via `paths.relpath` / `abspath`, so the
  DB stays portable. Do not store absolute paths. Skills run from `backend/`, so a
  relative `data/…` in a skill resolves to `backend/data/` — always let the backend
  resolve paths (`ect feedback apply --markdown …`) rather than writing into `data/`.
- **The live profile is `data/profile.md`, not `docs/profile.md`** as PRD §7.1 sketches.
  It accumulates employer, projects and interests, and the repo is pushed to GitHub, so
  it is personal state: gitignored, backed up with the rest of `data/`, and seeded from
  the tracked `docs/profile.example.md` by `paths.seed_profile` (which never overwrites).
  **It is append-only except for its last section.** *Language gaps to target* is a
  consolidated one-line-per-gap summary; PRD §7.1's blanket "additive edits only" no
  longer holds for it, and `/process-session` deletes a line there when a gap is fixed.
  *Current work* stays additive but is grouped by project, not by session — a new fact
  joins its project's bullet. See `docs/adr/0007-…`: the file is read *in full* on every
  `/generate-topic` run, so an append-only section restating one weakness per session
  costs its full price every run.
- **`data/learning-notes.md` is a second, separate hand-edited file** on the same
  contract (gitignored, seeded from `docs/learning-notes.example.md` by
  `paths.seed_notes`). It holds the durable coaching record and **owns all the language
  detail** — sentence patterns, phrases being activated, recurring corrections, delivery
  habits, word choice, how answers end, what is working. It is **not** folded into the
  profile on purpose: the profile is read in full on every `/generate-topic` run and has
  to stay short, while the notes are meant to grow. The split is *who is speaking*
  (profile) vs. *what has already been taught* (notes); a weakness appears in both only
  as one summary line there and the full entry here. Unlike the rest of the profile it is
  consolidated rather than append-only; `/process-session` prunes and merges it as well as
  adding to it — which is safe because `data/feedback/<id>.md` is the immutable per-session
  record underneath both files. It is
  the one file both Claude and the frontend write to (the Notes page, `GET`/`PUT
  /api/notes`), so a save carries the `version` it loaded and 409s rather than
  overwriting a newer one — see `services.write_notes`. Skills still edit the file
  directly rather than through `ect`, same as the profile.
- **Recordings are not in the data repo, and `ect archive` is what tracks them.** They
  were 102 MB against 1.8 MB for everything else, so `data/.gitignore` excludes the audio
  (ADR 0008). That means `git status` no longer answers "is this file still here and still
  itself" - `ect archive track` hashes each recording into `recording_archives`, and
  `./backup-recordings.ps1` copies them to the home server over rclone/sftp and confirms
  with `sha256sum -c` before recording `synced_at`. **Nothing sets `synced_at`
  automatically**: a transfer exiting 0 is not evidence the bytes arrived, and that flag is
  what would later license deleting a local copy. Originals are kept; `archive compress`
  exists but is opt-in, because the recordings are for listening back to.
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
- **The digest is derived, one-way, and never written by the relay** (`app/digest.py`).
  It is what the relay serves while the PC is off. Keeping it read-only is what keeps
  `services.write_notes`'s version/409 contract a local concern instead of a distributed
  one. It carries no audio, no transcripts and no per-word timings by construction - if
  you add something the offline UI needs, add it to `build_digest` *and* to the relay's
  route table in `relay/digest.go`, or the relay will 503 a route that now has data.
- **`services.decorate_session` is shared by the API and the digest builder** on
  purpose: the relay serves those rows verbatim, so a `has_*` flag computed differently
  in the two places shows up as the UI disagreeing with itself depending on which side
  answered.
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
- `PRD-worklog.md` — the worklog / interview-story extension (Phase 1 capture is built;
  rollup generation and `/interview-prep` are not)
- `docs/commands.md` — every skill and `ect` command, with payload shapes
- `docs/rubric.md` — scoring anchors; read before scoring anything
- `docs/setup.md` — prerequisites, VRAM ladder, config env vars, troubleshooting
- `docs/relay.md` — remote capture: the relay, `ect agent`, and how to run both
- `relay/README.md` — the Go switchboard itself; manifests live in the `k8s-config` repo
- `docs/adr/` — the load-bearing decisions and the alternatives rejected
