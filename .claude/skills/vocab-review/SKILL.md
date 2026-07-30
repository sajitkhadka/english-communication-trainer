---
name: vocab-review
description: Show the English Communication Trainer vocabulary state - which words are due for review, which are weakest, which have never been practised, and overall corpus stats. Use when the user asks what words are due, wants a vocabulary review, asks how their vocabulary is progressing, or runs /vocab-review.
---

# vocab-review

Read-only. Surface the vocabulary state so the user knows what to practise next.

```bash
cd backend
uv run ect vocab due --limit 20      # due words (lowest mastery first) + corpus stats
uv run ect vocab list --sort mastery --limit 30   # weakest words overall
```

Use `--sort recency`, `frequency`, or `due` if the user asks for a different cut.

## What to report

1. **Corpus line** - total words, how many due today, how many mastered (`mastery >=
   0.8`), how many never practised, average mastery.
2. **Due now** - a compact table: term, kind, meaning, mastery, days overdue. Put the
   weakest first. Flag anything carrying a `notes` field: that is a word with a recorded
   misuse, and it is the highest-value thing to practise.
3. **Quietly strong** - two or three words with high mastery, so progress is visible.
4. **Never practised** - terms with `times_seen = 0` that have been sitting in the
   corpus, if there are any.
5. **One recommendation** - the single next action, usually "run `/generate-topic` and
   these 3-4 words will be pulled in automatically".

Write nothing. This skill never modifies the database - scheduling changes only ever
come from `/process-session`, so review state stays tied to actual practice.

If the corpus is empty, say so and point at `/generate-topic` as the way to seed it.
