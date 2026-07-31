# English Communication Trainer

A local, self-hosted tool for improving professional English speaking: record practice
in the browser, transcribe and analyse it on your own GPU, and get linguistic feedback,
a rubric score, and spaced-repetition vocabulary from Claude Code skills.

Nothing leaves the machine. No transcription API, no feedback API — Whisper runs on the
GPU, and Claude runs in your console when you ask it to.

See [PRD.md](PRD.md) for the full specification.

## The idea

The work is split so each side does what it is good at:

- **The backend does everything deterministic and audio-heavy** — transcription, word
  timings, the pause map, filler counts, acoustic hesitation detection, WPM, target-word
  matching, SM-2 scheduling, the weighted score.
- **Claude does judgement only** — on pre-chewed structured data, never on audio.

That keeps token cost low and feedback quality high, and it means the numbers are
identical no matter how many times you rerun a session.

## The loop

```
/generate-topic          →  a topic + 5-6 target words, personalised from your profile
   record in the browser →  audio uploads, status: recorded
   press Process         →  transcribes on the GPU, status: pending
/process-session         →  feedback + score written, vocabulary rescheduled, profile updated
```

The frontend never calls Claude. It flags intent; you pull the trigger in the console
([ADR 0003](docs/adr/0003-queue-based-frontend-to-claude-handoff.md)).

## Quick start

```bash
# backend
cd backend
uv sync --extra dev
uv run ect db init
uv run ect doctor                       # GPU + ffmpeg + db check
uv run uvicorn app.main:app --reload --port 8000

# frontend (second terminal)
cd frontend
npm install
npm run dev                             # http://localhost:5173
```

Full prerequisites, model sizes, VRAM fallbacks and troubleshooting:
[docs/setup.md](docs/setup.md).

## Skills

| Skill | What it does |
|---|---|
| `/generate-topic [mode] [category]` | Creates a practice session with a topic and target vocabulary |
| `/process-session [id] [--force]` | Analyses queued sessions: feedback, score, vocabulary, profile |
| `/vocab-review` | Shows what is due, what is weakest, corpus stats |

Every command, argument, and payload shape: [docs/commands.md](docs/commands.md).

## How the analysis works

Whisper normalises speech and silently deletes "um" and "uh", so filler analysis that
reads only the transcript under-reports — invisibly. This tool runs two independent
layers and compares them:

- **WhisperX** (`large-v3` + wav2vec2 forced alignment) → the transcript and accurate
  word boundaries.
- **Silero VAD** → a speech/silence map derived from the audio alone.

A voiced span containing no aligned words is a sound that was spoken and dropped — a
hesitation reported even though no word exists for it. In testing, a clip whose "Umm"
was normalised out of the transcript still surfaced two acoustic hesitations.
([ADR 0002](docs/adr/0002-whisperx-plus-silero-vad.md))

The skill reads a **brief** rather than the raw transcript — numbered sentences with
each measurement attached to where it happened:

```
S3. [192.5 wpm] I basically, you know, decided to leverage a queue to smooth out the load.
    ! fillers: basically?, you know | pause 0.3s after "basically," mid-sentence | hesitation 0.4s (untranscribed)
```

`?` marks a context-dependent filler for the model to judge; `(untranscribed)` marks an
acoustic hesitation. A five-minute answer costs a few hundred tokens instead of several
thousand.

## Layout

```
backend/          FastAPI app, WhisperX/Silero pipeline, SQLite, the `ect` CLI
  app/            config, db, srs, services, routers, brief, cli
  pipeline/       audio, transcribe, vad, fillers, metrics, runner
  tests/          91 tests, no GPU required
frontend/         React + TypeScript (Vite)
.claude/skills/   generate-topic, process-session, vocab-review
data/             recordings, transcripts, feedback, prompts, profile, app.db  (gitignored)
docs/             rubric, commands, setup, profile.example.md, adr/
models/           downloaded model weights                            (gitignored)
```

Two notes where this differs from the PRD's sketch:

- Skills live in `.claude/skills/` rather than `skills/`, because that is where Claude
  Code discovers them. A skill in `skills/` would never load.
- The `sessions` and `words` tables carry a few additive columns beyond PRD §7.2
  (`repetitions` for SM-2, `transcribe_status` for the GPU stage, `notes`). Each is
  marked `ADDITIVE` in [backend/app/schema.sql](backend/app/schema.sql).

## Testing

```bash
cd backend && uv run pytest -q     # 91 tests, no GPU or model download needed
cd frontend && npm run build       # typecheck + production build
```

The analysis layer takes plain word and VAD values rather than reaching for the audio
itself, which is what makes it testable without a GPU.

## Status

Phase 1 and 2 of [PRD §15](PRD.md) are implemented, plus interview mode from Phase 3:
the full record → transcribe → analyse → score → reschedule loop, spaced repetition,
all seven frontend pages, and the in-frontend Process queue.

`data/profile.md` (seeded from `docs/profile.example.md`, gitignored) is still a template — it fills itself in as `/process-session` infers
facts about you, and topics get noticeably better once it has real content.
