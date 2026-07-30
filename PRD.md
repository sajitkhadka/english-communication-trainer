# PRD — English Communication Trainer

**Owner:** Sajit
**Status:** Draft v1
**Last updated:** 2026-07-30

---

## 1. Summary

A local, self-hosted tool to improve professional English communication, presentation delivery, and interview answering for a software engineer. The user records spoken practice in a browser; a local backend transcribes it (word-level timestamps + acoustic pause analysis) on an NVIDIA GPU; Claude Code, driven by skills, produces linguistic feedback, tracks learned vocabulary over time with spaced repetition, and generates personalized practice topics from a learned profile of the user.

The design deliberately splits work: **the backend does everything deterministic and audio-heavy; Claude does only judgment and language analysis on pre-chewed structured data.** This keeps Claude's token cost low and its output quality high.

---

## 2. Goals & Non-Goals

### Goals
- Practice speaking on (a) Claude-recommended topics with target vocabulary, and (b) the user's own free-form topics, including one-way interview-question answering.
- Reliable full transcript plus pause/hesitation analysis, without depending on the transcript to catch every filler.
- Persistent, low-token memory of learned vocabulary, surfaced by recency / frequency / mastery (spaced repetition).
- Structured feedback: filler analysis, better word/phrase/idiom swaps, sentence-structure improvements, target-word usage check, and a rubric score with history.
- A growing profile of the user, inferred from speech, that personalizes future topic generation.

### Non-Goals (v1)
- No two-way live conversation. Interview mode is one-way: Claude generates a question, the user answers, the answer is analyzed. Same pipeline as any topic.
- No speaker diarization. The user is the only speaker in every recording.
- No cloud APIs for transcription. Whisper runs locally on the GPU.
- No direct programmatic control of Claude Code from the frontend (see §6.3).

---

## 3. Users & Use Cases

Single user (the developer). Three practice intents, all sharing one pipeline:

1. **Recommended practice** — Claude generates a topic + a small set of brand-new target words/phrases/idioms. The user speaks, aiming to use them.
2. **Free-form practice** — The user picks a topic and speaks, using as much strong vocabulary as possible.
3. **Interview practice** — Claude generates an interview question (behavioral/technical/etc.); the user answers once. Treated as a recommended-practice variant with no target words required.

---

## 4. Core Flows

### 4.1 Recommended practice
1. User runs the topic-generation skill in the Claude Code console (optionally with a category, e.g. "system-design interview").
2. Skill reads `docs/profile.md` + due vocabulary from SQLite, writes a `session` row (status `awaiting_recording`) and a prompt file the backend serves.
3. Frontend renders the topic + target words. User hits **Record** (browser `MediaRecorder`), stops, and the audio uploads to the backend.
4. Backend stores the audio, marks the session `recorded`, and (on process) transcribes.
5. User clicks **Process** in the frontend → session flagged `pending`. User runs the processing skill in the console → Claude reads the enriched transcript JSON and writes feedback.
6. Feedback + score render on the frontend; vocabulary and profile update in SQLite / `profile.md`.

### 4.2 Free-form practice
Same as 4.1 but the user provides the topic (typed in the frontend or spoken freely), no target words, stored/displayed under a separate section. Target-word usage check is skipped.

### 4.3 Interview practice
Same as 4.1; the "target words" slot is empty and the topic is a single interview question. Optional model-answer comparison in a later phase.

---

## 5. Transcription & Audio Analysis

### 5.1 Stack
- **WhisperX** (built on faster-whisper) with model `large-v3`, `compute_type=int8_float16`. Provides transcription **and** wav2vec2 forced alignment for accurate word-level timestamps.
- **Silero VAD** for a precise speech/silence segmentation map, independent of the transcript.
- CTranslate2 / faster-whisper backend for GPU inference.

### 5.2 Why this design (the "don't drop fillers" problem)
All Whisper-family models normalize speech and silently drop some disfluencies ("um / uh / like"). We do **not** rely on the transcript to detect hesitation. Instead:
- WhisperX gives precise **word boundaries**.
- Silero VAD gives every **silence gap** and its duration, plus vocalized segments.
- A vocalized filler that Whisper omits still appears as a **speech segment with no aligned word** → a reliable hesitation signal even when the word isn't in the text.

This makes pause/hesitation analysis robust regardless of transcript cleanup. Textual fillers that *are* captured are counted too; the acoustic layer covers the rest.

### 5.3 GPU sizing (RTX 3060 Laptop, 6 GB VRAM, driver 610.62 / CUDA 13.3)
- `large-v3` @ `int8_float16` ≈ 3 GB → fits with headroom for the wav2vec2 alignment model (~0.3 GB) and Silero VAD.
- Fallback if OOM or driver issues: `large-v3` @ `int8` (~2 GB) or `medium` @ `float16`.
- Expected throughput: several× real-time; a 3–5 min clip transcribes + aligns in well under a minute.

### 5.4 Backend-computed metrics (per recording)
Precomputed so Claude never touches audio:
- Full transcript + per-word timestamps.
- WPM (overall and per-segment).
- Filler counts + locations (textual) and hesitation segments (acoustic, from VAD gaps).
- Pause map: gap durations, count, longest pause, pause-per-minute.
- Sentence segmentation.
- (Recommended sessions) the list of target words to check.

Output: one compact `transcript.json` per recording — the sole input Claude reads.

---

## 6. Architecture

### 6.1 Components
- **Frontend** — React + TypeScript. Records audio, lists past recordings by section (recommended / free-form / interview), shows subtitles synced to playback, renders feedback + score history, shows the learned-words page and the (initially empty) suggestions section.
- **Backend** — Python + FastAPI. Serves prompts, receives/stores audio, runs WhisperX + Silero, writes `transcript.json`, serves feedback, exposes REST for the frontend, owns the SQLite DB.
- **Claude Code skills** — topic generation, processing/feedback, profile update, vocabulary maintenance. Run from the console by the user.
- **SQLite** — vocabulary, sessions, scores, spaced-repetition state.
- **File tree** — audio, transcripts, feedback markdown, `docs/`.

### 6.2 Processing model
Backend does transcription + metrics → structured JSON. Claude does linguistic judgment on that JSON. Clean separation, minimal tokens.

### 6.3 Frontend ↔ Claude Code (queue, not RPC)
Claude Code is not a service the frontend can call, and the headless SDK route is the cloud API we're avoiding. Instead the frontend expresses **intent** and the user pulls the **trigger**:
- Frontend "Process" button sets a session's status to `pending` (SQLite row / `queue/` marker).
- User runs the processing skill in the console; it picks up all `pending` sessions, processes, and writes results back.
- Zero API tokens, full user control. This is the v1 contract.

---

## 7. Data Model

### 7.1 Storage decision (least tokens)
- **Vocabulary corpus → SQLite**, queried by a small skill script. A flat context file is the worst option (Claude must load the whole growing corpus every session). RAG/MCP is overkill at this scale (hundreds–low thousands of items, no semantic-search need) and burns retrieval tokens. SQLite lets a skill run one query ("15 words due for review") and hand Claude only that slice — the context window holds 15 words, never 1,500.
- **Profile → `docs/profile.md`** (markdown, not DB). Small, prose, read in full on every topic generation. Right tool for a different job.

### 7.2 SQLite schema (initial)
```sql
-- Vocabulary with spaced-repetition state (SM-2 style)
CREATE TABLE words (
  id            INTEGER PRIMARY KEY,
  term          TEXT UNIQUE NOT NULL,
  kind          TEXT,          -- word | phrase | idiom
  meaning       TEXT,
  example       TEXT,
  first_seen    TEXT,          -- ISO date
  last_practiced TEXT,
  times_seen    INTEGER DEFAULT 0,
  times_used_correctly INTEGER DEFAULT 0,
  ease          REAL DEFAULT 2.5,   -- SM-2 ease factor
  interval_days INTEGER DEFAULT 0,
  due_date      TEXT,
  mastery       REAL DEFAULT 0,     -- derived 0..1
  source        TEXT           -- recommended | user_speech
);

CREATE TABLE sessions (
  id            INTEGER PRIMARY KEY,
  mode          TEXT NOT NULL,      -- recommended | freeform | interview
  category      TEXT,
  topic         TEXT,
  target_words  TEXT,               -- JSON array of word ids/terms
  status        TEXT NOT NULL,      -- awaiting_recording | recorded | pending | processed
  audio_path    TEXT,
  transcript_path TEXT,
  feedback_path TEXT,
  created_at    TEXT,
  processed_at  TEXT
);

CREATE TABLE scores (
  id            INTEGER PRIMARY KEY,
  session_id    INTEGER REFERENCES sessions(id),
  vocab_range   REAL, filler_density REAL, fluency REAL,
  grammar       REAL, structure REAL, coherence REAL,
  target_usage  REAL, overall REAL,
  created_at    TEXT
);

CREATE TABLE word_session_usage (   -- which words appeared/were used well per session
  word_id       INTEGER REFERENCES words(id),
  session_id    INTEGER REFERENCES sessions(id),
  used          INTEGER,            -- 0/1 appeared
  used_correctly INTEGER            -- 0/1
);
```

### 7.3 File tree (`data/`)
```
data/
  recordings/
    recommended/<session_id>.wav
    freeform/<session_id>.wav
    interview/<session_id>.wav
  transcripts/<session_id>.json
  feedback/<session_id>.md
  prompts/<session_id>.json        # topic + target words the frontend renders
  queue/                            # optional file-based pending markers
  app.db                           # SQLite
```

---

## 8. Skills & Command Reference

All run from the Claude Code console. Detailed command docs live in `docs/commands.md`.

| Skill | Purpose | Reads | Writes |
|---|---|---|---|
| `generate-topic` | Create a topic (+ target words for recommended/interview). Accepts a category arg. | `profile.md`, due words (SQLite) | `sessions` row, `prompts/<id>.json` |
| `process-session` | Analyze recorded session(s); produce feedback + score; auto-update the profile. | `transcripts/<id>.json`, session target words | `feedback/<id>.md`, `scores`, word updates, `docs/profile.md` |
| `vocab-review` | Surface due words / stats for the next recommended session. | SQLite | (returns slice) |

**`process-session` arguments:**
- No argument → process every session flagged `pending`.
- `<session_id>` → process just that session. If it's already processed, no-op with a clear "nothing to process" message.
- `--force` → deliberately reprocess an already-processed session.

**`process-session` output contract (per session):** filler analysis (textual + acoustic), better-word/phrase/idiom swaps, sentence-structure suggestions, target-word usage check with corrective examples if misused, a **model answer** (a stronger rewrite of what the user actually said, for every mode — the most actionable part of the feedback), a rubric score, and new vocabulary to add. It must **not** track basic words — only professional/elevating terms, and words the user under-uses or misuses.

**Automatic profile update:** as part of every `process-session` run, Claude folds any newly inferred facts about the user (role, employer, interests, projects) into `docs/profile.md`. No separate skill or manual step — the profile stays current session-to-session. Updates are additive and only when something new/relevant is inferred.

---

## 9. Scoring Rubric

Each session scored 0–10 per dimension; `overall` is a weighted mean. History drives progress charts on the frontend.

- **Vocabulary range** — variety and level of professional terms; use of target words.
- **Filler density** — fillers + hesitations per minute (from §5.4); lower is better.
- **Fluency / pace** — WPM in target band, pause distribution, rhythm.
- **Grammar** — accuracy of tense, agreement, articles, prepositions.
- **Sentence structure** — variety, clause construction, avoidance of run-ons/fragments.
- **Coherence** — logical flow and organization of the answer.
- **Target-word usage** — used, and used correctly (recommended/interview only; N/A for free-form).

Full scoring instructions and anchors live in `docs/rubric.md` so scores stay consistent across sessions. Alongside the score, every session's feedback includes a **model answer** — a polished rewrite of what the user actually said — so the score has a concrete target to compare against.

---

## 10. Spaced Repetition

Model the user's described intent ("recency, frequency, struggled-with, needs-practice") with **SM-2** (or Leitner as a simpler alt). On correct use, ease/interval grow and `due_date` pushes out; on misuse or non-use, the word resurfaces sooner. `generate-topic` pulls a blend of **due** words and a few **brand-new** words each session. `mastery` is derived for display on the learned-words page.

---

## 11. Frontend Pages

- **Home / Practice** — start recommended, free-form, or interview; record UI.
- **Recommended** — list of recommended sessions with topic, target words, status, score.
- **Free-form** — separate list of free-form sessions.
- **Interview** — separate list of interview sessions.
- **Session detail** — audio player with synced subtitles, transcript, pause map, feedback markdown, score breakdown.
- **Vocabulary** — all learned words with kind, meaning, example, mastery, due status; sortable by recency/frequency/mastery.
- **Suggestions** — populated only after Claude generates words/topics; empty by default. Can request a category.
- **Progress** — score trends over time (charts).

---

## 12. Repository Structure

```
repo/
  backend/            # FastAPI app, WhisperX/Silero pipeline, DB access
    app/
    pipeline/
    tests/
  frontend/           # React + TypeScript
    src/
  skills/             # generate-topic, process-session, update-profile, vocab-review
  data/               # see §7.3 (gitignored except structure)
  docs/
    profile.md
    commands.md
    rubric.md
    setup.md
    adr/
      0001-sqlite-over-context-file-or-rag.md
      0002-whisperx-plus-silero-vad.md
      0003-queue-based-frontend-to-claude-handoff.md
      # add more only for load-bearing decisions, as they come up
  README.md
```

---

## 13. Architecture Decision Records (`docs/adr/`)

Keep ADRs lightweight — author one only for a decision that's genuinely load-bearing and would be expensive to reverse. Don't document every choice. The ones worth recording:

1. **SQLite over context file or RAG** — cheap filtering by recency/frequency/mastery; loads only the queried slice into context. RAG/MCP unjustified at this scale.
2. **WhisperX + Silero VAD** — accurate word timestamps + transcript-independent acoustic pause/hesitation detection solves the "don't drop fillers" requirement.
3. **Queue-based frontend→Claude handoff** — frontend flags `pending`; user runs the skill. Avoids cloud API and keeps the user in control.

Everything else (Python backend, no diarization, one-way interview mode) is noted in this PRD and doesn't need a standalone ADR unless it's revisited later.

---

## 14. Setup Notes (`docs/setup.md`)

- CUDA-enabled PyTorch matching the installed driver; WhisperX, faster-whisper (CTranslate2), Silero VAD.
- Model download: `large-v3` + wav2vec2 English alignment model (cached locally).
- Config for `compute_type` with documented fallbacks (§5.3).
- FastAPI run instructions; frontend dev server; DB init/migration script.

---

## 15. Phased Delivery

- **Phase 1 — Core loop:** backend audio ingest + WhisperX/Silero + `transcript.json`; SQLite; `generate-topic` and `process-session` skills (with model answer + auto profile update); frontend record/list/detail for recommended + free-form; rubric + scores.
- **Phase 2 — Memory & personalization:** spaced repetition in `generate-topic`; vocabulary page; suggestions/category requests; progress charts; **in-frontend "Process" queue-runner** (button flags `pending`, plus a view of pending/processed status).
- **Phase 3 — Interview mode & polish:** interview section; subtitle sync refinements; export/report.

---

## 16. Open Questions / Future

- Long-term: if the vocabulary corpus ever reaches many thousands with a need for semantic lookup, revisit embeddings/RAG (not before — not a current concern).
