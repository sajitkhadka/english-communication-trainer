# ADR 0004 — Worklog: layered distillation inside ECT, not a separate tool or RAG

**Status:** Accepted · **Date:** 2026-08-14 · **PRD:** PRD-worklog.md

## Context

The user wants a daily ~10-minute spoken journal — what happened at work, decisions,
hurdles, wins, what's upcoming — captured through the same record→transcribe flow, and
accumulated into a memory that can later answer "organise everything I did this quarter"
and produce behavioral-interview (STAR) answers grounded in real events.

Two questions are load-bearing:

1. **Where does this live** — inside this repo, or as a new project?
2. **How does Claude read months of journal** without loading hundreds of documents?
   A 10-minute recording is ~1,500 words (~2k tokens); a year is 250+ recordings. Any
   design where synthesis reads raw material grows linearly and eventually cannot run.

## Decision

**Extend ECT with a fourth session mode, `worklog`,** reusing the recording UI, the
WhisperX/Silero pipeline, and the session brief unchanged. On top of it, a **three-layer
distillation**, each layer written once and bounded in size:

1. **Daily entry** (~300 tokens). At capture time — while the context is one session and
   one brief — a skill extracts a structured markdown entry: projects, what was done,
   decisions *with the why*, hurdles, wins, upcoming, competency tags. Written through a
   single CLI path (`ect worklog add`), which files it under `data/worklog/daily/` and
   indexes it in SQLite (date, projects, tags, one-line summary, path).
2. **Monthly rollup** (~1k tokens). Once a month, a skill compacts that month's dailies
   into one file organised by project and theme — not by date — with each claim carrying
   a date pointer back to its daily entry (`→ 2026-08-14`).
3. **Synthesis on demand.** Quarterly reviews and interview prep read the monthly
   rollups (3–4k tokens per quarter), pick the strong stories, and follow pointers into
   the handful of daily entries that matter. Targeted questions ("my conflict stories")
   skip the rollups entirely and query the index by tag.

This is `app/brief.py`'s idea applied recursively: Claude never reads raw material, only
a compact rendering with citations back to the full data, at every level.

## Alternatives

**A separate project.** Duplicates the GPU pipeline, the recording frontend, and the
"personal state lives in gitignored `data/`" convention for no benefit — and severs the
best part: captured stories feeding back into `generate-topic` as behavioral practice
questions, so the user rehearses *telling* their own stories through the existing
scoring loop. Rejected.

**Synthesis reads the raw transcripts or all daily files.** The flat-file failure mode
ADR 0001 already rejected for vocabulary, one level up: cost grows linearly forever, and
the model spends its window re-deriving structure that could have been extracted once.
Rejected on the same grounds.

**RAG / embeddings over the journal.** The retrieval needs are exact filters — date
range, tag, project — which the index table answers with a `WHERE` clause. Same
verdict as ADR 0001: revisit only if a genuine semantic-search need appears at a scale
the index cannot serve.

**One rolling summary file that gets rewritten.** A single "everything so far" document
stays small, but compaction is lossy and irreversible — the detail an interview answer
needs (numbers, names, the specific why) is exactly what gets squeezed out first, with
no pointer left to recover it. The layered scheme loses nothing: every layer keeps
pointers down, and the full transcript stays on disk.

## Consequences

- No synthesis task ever reads more than a few thousand tokens, regardless of how many
  years accumulate. Token cost is bounded by design, not by discipline.
- The daily extraction is the one layer that cannot be cheaply redone later — recall of
  the *why* decays in days. The skill must capture decisions, reasons, and impact on the
  day. (Transcripts are kept, so re-extraction is possible, but costs a Claude pass per
  session and loses nothing said aloud — only what was never said.)
- Rollups are derived artifacts: regenerable from the dailies at any time, so a bad
  rollup is never a data loss.
- Competency tags must come from a small controlled list (defined in PRD-worklog §6) or
  tag-based retrieval fragments into synonyms.
- Follows the existing seams: skills touch the journal only through `ect worklog …`
  subcommands (no SQL, no direct writes into `data/`), and the entry write is a single
  path mirroring `services.record_feedback`.
