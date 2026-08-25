# PRD — Worklog & Interview-Story Memory

**Owner:** Sajit
**Status:** Draft v1
**Last updated:** 2026-08-14
**Extends:** PRD.md (English Communication Trainer) · **ADR:** docs/adr/0004

---

## 1. Summary

A spoken daily work journal built on the existing ECT pipeline. At the end of the day
the user records ~10 minutes — what they did, decisions and why, hurdles, wins, what's
upcoming. The backend transcribes it like any session; a skill distils the brief into a
structured daily entry; monthly rollups compress the dailies; and synthesis skills turn
the accumulated journal into quarterly reviews, brag documents, and behavioral-interview
(STAR) answers grounded in real events.

Two ideas carry the design:

1. **Same split as ECT** — the backend does everything deterministic (capture,
   transcription, storage, indexing); Claude does judgement only (what is story-worthy,
   which competencies an event evidences, how to tell it).
2. **Layered distillation** (ADR 0004) — transcript (~2k tokens) → daily entry (~300) →
   monthly rollup (~1k) → synthesis reads rollups and follows date pointers selectively.
   No task ever loads the whole journal, no matter how many years accumulate.

---

## 2. Goals & Non-Goals

### Goals
- Frictionless daily capture: record, press Process, run one skill. Under a minute of
  interaction beyond the talking itself.
- Preserve the *why* behind decisions and the *impact* of outcomes on the day they
  happen — the material behavioral answers are made of and the first thing memory loses.
- Chronological archive that is human-readable without the app (plain markdown files).
- Bounded-cost synthesis: quarterly review or interview prep reads a few thousand
  tokens, never hundreds of files.
- Feed captured stories back into `generate-topic` so behavioral practice questions come
  from the user's own history — closing the loop with the trainer.

### Non-Goals (v1)
- Not a task tracker or todo system. It is retrospective narrative; "upcoming" is
  context for tomorrow's entry, not managed state.
- No automatic scheduling or reminders. The user starts the recording.
- No rubric scoring of worklog sessions by default (see §5.1) — the deliverable is the
  entry, not language feedback. English analysis remains available on request.
- No semantic search / embeddings (ADR 0004).

---

## 3. Use Cases

1. **Daily capture** — end of day, talk for ~10 minutes, entry appears in the journal.
2. **Catch-up capture** — missed a day or two; one recording talks through several
   days at once ("yesterday I…, today I…") and produces one entry per day, same as if
   each had been recorded separately.
3. **Targeted retrieval** — "what did I do on the payment migration?" → targeted
   retrieval by project from the index.
4. **Periodic review** — monthly/quarterly "organise what I've been doing" → rollups.
5. **Interview prep** — "give me STAR answers about conflict" or a mock behavioral
   round built from the user's actual events, with dates and details.
6. **Practice the telling** — a generated behavioral question becomes an `interview`
   session; the existing pipeline scores the spoken delivery of the user's own story.

---

## 4. Core Flows

### 4.1 Daily capture
1. User records under **Worklog** in the frontend (mode `worklog`, no topic, no target
   words) and presses **Process** — identical to the existing flow: transcribe, then
   status `pending`.
2. User runs `/log-work` in the console. The skill reads `ect session brief <id>`,
   extracts the structured entry (§6.1), and applies it with `ect worklog add`. The
   backend files the markdown under `data/worklog/daily/`, upserts the index row, links
   the session, and flips it to `processed`.
3. If a second recording lands on the same date, the skill merges into that day's
   existing entry rather than creating a sibling file.
4. **Missed a day or two?** One recording can talk through several calendar days at
   once. The skill splits the brief's content by the day it's about (resolving
   relative references like "yesterday" against the session's recorded timestamp) and
   calls `ect worklog add` once per date — each date still merges with any existing
   entry exactly as in (3). Nothing about the backend changes for this case: a session
   simply ends up linked to whichever date's entry was filed last, and every date gets
   its own file and index row regardless of how many calendar days back it covers.

### 4.2 Monthly rollup
1. `/log-work` finishes by checking `ect worklog rollup status`; if a completed month
   has no rollup it says so. The user runs `/worklog-rollup [YYYY-MM]` when ready.
2. The skill reads that month's daily entries (~22 × 300 tokens), writes one rollup
   organised by project and theme with `→ YYYY-MM-DD` pointers on every claim, and
   applies it with `ect worklog rollup add`. Rollups are derived artifacts —
   regenerating one overwrites it and loses nothing.

### 4.3 Synthesis and interview prep
1. User runs `/interview-prep` with a competency, a question, or a period ("Q3",
   "brag doc for review season").
2. The skill queries the index (`ect worklog list --tag conflict`), reads the relevant
   monthly rollups, follows date pointers into only the daily entries it needs, and
   produces STAR-formatted answers / a brag document / mock questions with model
   answers. Outputs it deems worth keeping go to `data/worklog/prep/<slug>.md`.
3. Optionally hands a generated question to `generate-topic` as an `interview` session
   so the user practices delivering the answer aloud (§3.5).

---

## 5. Relationship to the Existing System

### 5.1 Session mode, status, scoring
- `sessions.mode` gains `worklog`. Both status fields behave exactly as today
  (`awaiting_recording → recorded → pending → processed`; `transcribe_status`
  independent). The queue contract of ADR 0003 is unchanged — `/log-work` is to worklog
  sessions what `/process-session` is to practice sessions.
- Worklog sessions get **no score row**. Scoring a journal entry rewards performing
  rather than reporting, and the rubric's dimensions (target usage, structure anchors)
  presume practice intent. `/log-work --with-feedback` opts into the standard language
  analysis as a separate additive step; it never gates the entry.

### 5.2 Privacy
Everything under `data/worklog/` is personal state in the strongest sense (employer,
colleagues, unreleased work): gitignored with the rest of `data/`, backed up with it,
never in the repo. Same rule as `data/profile.md`.

---

## 6. Data Model

### 6.1 Daily entry (`data/worklog/daily/YYYY-MM-DD.md`)

```markdown
# 2026-08-14
projects: payment-migration, oncall
tags: debugging, cross-team, ownership

## Did
- …2–4 bullets, concrete…

## Decisions — and why
- Chose X over Y because …        ← the interview gold; never omit the why

## Hurdles
- …including how it was (or wasn't) resolved…

## Wins / impact
- …with numbers where they exist…

## Upcoming
- …context for tomorrow, not tracked state…
```

Sections may be empty but are always present; the skill writes `-` for nothing to
report. One file per date; same-day additions merge.

**Competency tags** come from a controlled list (free tags fragment retrieval):
`leadership · ownership · conflict · ambiguity · cross-team · debugging ·
design-tradeoff · mentoring · failure · delivery · influence`. Extending the list is a
deliberate edit to this PRD, not a per-entry improvisation.

### 6.2 Rollup (`data/worklog/monthly/YYYY-MM.md`)
Organised by project/theme, not date. Every claim carries `→ YYYY-MM-DD` pointers. Ends
with a **Story candidates** section: the 3–5 events that month with interview potential
and which competencies they evidence.

### 6.3 SQLite (additive; needs explicit `ALTER`/`CREATE` on existing DBs)

```sql
CREATE TABLE worklog_entries (
  id           INTEGER PRIMARY KEY,
  entry_date   TEXT UNIQUE NOT NULL,      -- ISO date, one row per day
  session_id   INTEGER REFERENCES sessions(id),  -- latest contributing session
  projects     TEXT,                      -- JSON array
  tags         TEXT,                      -- JSON array, controlled list (§6.1)
  summary      TEXT,                      -- one line, for lists and retrieval
  path         TEXT NOT NULL,             -- repo-relative markdown path
  created_at   TEXT, updated_at TEXT
);

CREATE TABLE worklog_rollups (
  id           INTEGER PRIMARY KEY,
  month        TEXT UNIQUE NOT NULL,      -- 'YYYY-MM'
  path         TEXT NOT NULL,
  created_at   TEXT
);
```

### 6.4 File tree

```
data/worklog/
  daily/YYYY-MM-DD.md      # source of truth, written once, merged same-day
  monthly/YYYY-MM.md       # derived, regenerable
  prep/<slug>.md           # synthesis outputs worth keeping
```

---

## 7. Commands & Skills

CLI first, as always: skills contain no SQL and never write into `data/` themselves.
The backend resolves all paths.

| Command | Does |
|---|---|
| `ect worklog add --markdown <file> --date <d> --projects <csv> --tags <csv> --summary <line> [--session <id>]` | Validates tags against the controlled list, files/merges the daily entry, upserts the index row, links + completes the session. The single write path. |
| `ect worklog list [--month M] [--from/--to] [--tag T] [--project P]` | Index rows: date, projects, tags, one-liner, path. |
| `ect worklog show <date \| YYYY-MM>` | Prints a daily entry or a month's rollup. |
| `ect worklog rollup status` | Completed months lacking a rollup. |
| `ect worklog rollup add --month M --markdown <file>` | Files/overwrites a monthly rollup. |

| Skill | Reads | Writes |
|---|---|---|
| `/log-work` | pending `worklog` briefs | daily entry via `ect worklog add`; nudges on missing rollups |
| `/worklog-rollup [M]` | `ect worklog show` per day of M | rollup via `ect worklog rollup add` |
| `/interview-prep <ask>` | index query → rollups → targeted dailies | console; keepers to `data/worklog/prep/` |

`/log-work` extraction instructions must insist on: the *why* behind every decision,
impact with numbers when stated, and named projects normalised to a consistent slug —
these are the three things that make month-later retrieval work.

---

## 8. Frontend

Minimal. A **Worklog** section beside Recommended / Free-form / Interview: record UI
(no topic field), session list, and a detail view that renders the day's entry markdown
in place of the feedback pane. Rollups and prep outputs are console/files territory in
v1 — no dedicated UI.

---

## 9. Token Budget (the design's invariant)

| Task | Reads | ≈ tokens |
|---|---|---|
| `/log-work` (daily) | one brief | 1–2k |
| `/worklog-rollup` | ~22 daily entries | 7k |
| Quarterly review | 3 rollups + ~5 followed dailies | 5k |
| `/interview-prep --tag X` | index query + ~6 dailies | 2–3k |

Every layer bounds the next; nothing scales with the age of the journal. If a change
breaks this table, it is the wrong change (ADR 0004).

---

## 10. Phased Delivery

- **Phase 1 — Capture:** schema, `ect worklog add/list/show`, `worklog` mode end to
  end, `/log-work`, frontend Worklog section. *Use it daily for a few weeks before
  building more — rollup design improves with real entries to roll up.*
- **Phase 2 — Rollups:** `rollup-status`, `rollup add`, `/worklog-rollup`, the nudge in
  `/log-work`.
- **Phase 3 — Synthesis:** `/interview-prep`, `data/worklog/prep/`, and the
  `generate-topic` hook that turns story candidates into behavioral practice sessions.

---

## 11. Open Questions / Future

- Should `/log-work --with-feedback` results feed the vocabulary SRS like practice
  sessions do? Deferred until the flag sees real use.
- Yearly rollups (rolling up the rollups) — same pattern one level higher; not needed
  before year two.
- Export: a brag-doc template tuned to the user's actual review cycle, once one exists.
