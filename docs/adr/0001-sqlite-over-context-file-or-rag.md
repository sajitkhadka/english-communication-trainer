# ADR 0001 — SQLite for the vocabulary corpus, not a context file or RAG

**Status:** Accepted · **Date:** 2026-07-30 · **PRD:** §7.1, §10

## Context

The tool must remember every word, phrase, and idiom the user has learned, and resurface
them by recency, frequency, and mastery (spaced repetition). The corpus grows for as long
as the tool is used — hundreds to low thousands of items over a year of daily practice.

Whatever holds it is read by a Claude Code skill on every topic generation, so its shape
directly sets the token cost of the core loop.

## Decision

Store vocabulary in **SQLite**, and have a skill query only the slice it needs.

Store the user profile as prose in **`docs/profile.md`** instead — different job,
different tool.

## Alternatives

**A flat markdown/context file.** Simplest to write, worst to live with: Claude must load
the entire corpus on every generation, so cost grows linearly with vocabulary size, and
"which 15 words are due today" becomes something the model has to work out by reading
1,500 rows. Ordering by mastery or due date is not something a text file can do.

**RAG / embeddings, or an MCP server over the corpus.** Overkill at this scale. There is
no semantic-search need — the queries are `due_date <= today ORDER BY mastery` and
similar exact filters, which is precisely what SQL is for. It would add an index to keep
in sync, an embedding model to run, and retrieval tokens to pay, to answer questions a
`WHERE` clause answers exactly. Worth revisiting only if the corpus reaches many
thousands *and* a genuine "find me words like this one" need appears (PRD §16).

**Postgres or another server DB.** Nothing here needs concurrency, network access, or a
running daemon. SQLite is a file, which suits a single-user local tool and makes the
whole state trivially backed up by copying `data/`.

## Consequences

- `generate-topic` costs one query and ~15 rows of context regardless of corpus size.
  The context window holds 15 words, never 1,500.
- The SM-2 arithmetic lives in `app/srs.py`, not in a prompt, so review scheduling is
  identical no matter which session it came from and cannot drift between skill runs.
- Sorting by recency/frequency/mastery for the Vocabulary page is free.
- Cost: a schema to migrate as the model evolves, and skills that must shell out to
  `ect` rather than reading a file. Both are handled by keeping the CLI as the single
  seam — no skill contains SQL.
- `profile.md` stays markdown because it is small, read whole, and prose; putting it in
  the DB would gain nothing and lose diffability.
