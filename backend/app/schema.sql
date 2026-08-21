-- English Communication Trainer schema (PRD 7.2).
-- Applied idempotently by `ect db init`. Columns marked ADDITIVE were not in the
-- PRD's initial sketch but are needed by the SM-2 implementation or the pipeline.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Vocabulary with spaced-repetition state (SM-2 style)
CREATE TABLE IF NOT EXISTS words (
  id                   INTEGER PRIMARY KEY,
  term                 TEXT UNIQUE NOT NULL,
  kind                 TEXT,                -- word | phrase | idiom
  meaning              TEXT,
  example              TEXT,
  first_seen           TEXT,                -- ISO date
  last_practiced       TEXT,
  times_seen           INTEGER DEFAULT 0,
  times_used_correctly INTEGER DEFAULT 0,
  ease                 REAL    DEFAULT 2.5, -- SM-2 ease factor
  interval_days        INTEGER DEFAULT 0,
  due_date             TEXT,
  mastery              REAL    DEFAULT 0,   -- derived 0..1
  source               TEXT,                -- recommended | user_speech
  repetitions          INTEGER DEFAULT 0,   -- ADDITIVE: SM-2 successful-review streak
  notes                TEXT                 -- ADDITIVE: misuse notes from feedback
);

CREATE INDEX IF NOT EXISTS idx_words_due     ON words(due_date);
CREATE INDEX IF NOT EXISTS idx_words_mastery ON words(mastery);

CREATE TABLE IF NOT EXISTS sessions (
  id              INTEGER PRIMARY KEY,
  mode            TEXT NOT NULL,            -- recommended | freeform | interview | worklog | brainstorm | journal
  category        TEXT,
  topic           TEXT,
  target_words    TEXT,                     -- JSON array of terms
  status          TEXT NOT NULL,            -- awaiting_recording | recorded | pending | processed
  audio_path      TEXT,
  transcript_path TEXT,
  feedback_path   TEXT,
  created_at      TEXT,
  processed_at    TEXT,
  -- ADDITIVE: the GPU pipeline runs independently of the Claude handoff, so it
  -- needs its own status. `status` tracks the user/Claude workflow only.
  transcribe_status TEXT DEFAULT 'none',    -- none | running | done | error
  transcribe_error  TEXT,
  duration_sec      REAL,
  notes             TEXT,                   -- ADDITIVE: user's own note / typed prompt context
  -- ADDITIVE: a short Claude-authored title + one-line summary, written back once a
  -- session is processed (or, for `journal`, never - there is no processing). Doubles
  -- as the UI's compact label and the human-readable part of the output filename.
  title             TEXT,
  summary           TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_mode   ON sessions(mode);

CREATE TABLE IF NOT EXISTS scores (
  id             INTEGER PRIMARY KEY,
  session_id     INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
  vocab_range    REAL,
  filler_density REAL,
  fluency        REAL,
  grammar        REAL,
  structure      REAL,
  coherence      REAL,
  target_usage   REAL,
  overall        REAL,
  created_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_scores_session ON scores(session_id);

-- Which words appeared / were used well per session
CREATE TABLE IF NOT EXISTS word_session_usage (
  word_id        INTEGER REFERENCES words(id) ON DELETE CASCADE,
  session_id     INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
  used           INTEGER,                   -- 0/1 appeared
  used_correctly INTEGER,                   -- 0/1
  PRIMARY KEY (word_id, session_id)
);

-- ADDITIVE: Claude-generated topic suggestions the frontend's Suggestions page
-- reads. Empty until a skill writes to it (PRD 11).
CREATE TABLE IF NOT EXISTS suggestions (
  id           INTEGER PRIMARY KEY,
  mode         TEXT NOT NULL,               -- recommended | freeform | interview
  category     TEXT,
  topic        TEXT NOT NULL,
  target_words TEXT,                        -- JSON array of terms
  rationale    TEXT,
  created_at   TEXT,
  consumed_by  INTEGER REFERENCES sessions(id) ON DELETE SET NULL
);

-- ADDITIVE: the frontend can ask for a category ("system-design interview") without
-- Claude running; the skill drains this on its next run.
CREATE TABLE IF NOT EXISTS suggestion_requests (
  id         INTEGER PRIMARY KEY,
  mode       TEXT NOT NULL,
  category   TEXT,
  status     TEXT NOT NULL DEFAULT 'open',  -- open | fulfilled
  created_at TEXT
);

-- ADDITIVE (PRD-worklog 6.3): index over the daily work-journal entries in
-- data/worklog/daily/. The markdown files are the source of truth; these rows make
-- "my conflict stories" a WHERE clause instead of a crawl. The entry outlives the
-- session it came from - the journal is the archive, the session is just the capture.
CREATE TABLE IF NOT EXISTS worklog_entries (
  id         INTEGER PRIMARY KEY,
  entry_date TEXT UNIQUE NOT NULL,          -- ISO date; one row per day, same-day adds merge
  session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
  projects   TEXT,                          -- JSON array of project slugs
  tags       TEXT,                          -- JSON array from the controlled list (PRD-worklog 6.1)
  summary    TEXT,                          -- one line, for lists and retrieval
  path       TEXT NOT NULL,                 -- repo-relative markdown path
  created_at TEXT,
  updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_worklog_date ON worklog_entries(entry_date);

-- ADDITIVE (PRD-worklog 6.2): monthly rollups are derived artifacts, regenerable
-- from the dailies at any time.
CREATE TABLE IF NOT EXISTS worklog_rollups (
  id         INTEGER PRIMARY KEY,
  month      TEXT UNIQUE NOT NULL,          -- 'YYYY-MM'
  path       TEXT NOT NULL,
  created_at TEXT
);
