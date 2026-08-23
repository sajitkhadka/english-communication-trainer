---
name: vocab-review
description: Show the English Communication Trainer vocabulary state - which words are due for review, which are active versus passive (recognised but never actually spoken), which are weakest, which have never been practised, and overall corpus stats. Use when the user asks what words are due, wants a vocabulary review, asks about their active vocabulary, asks how their vocabulary is progressing, or runs /vocab-review.
---

# vocab-review

Read-only. Surface the vocabulary state so the user knows what to practise next.

```bash
cd backend
uv run ect vocab due --limit 20      # due words (lowest mastery first) + corpus stats
uv run ect vocab gaps --limit 20     # active vs. passive vocabulary
uv run ect vocab list --sort mastery --limit 30   # weakest words overall
```

Use `--sort recency`, `frequency`, or `due` if the user asks for a different cut, and
`ect vocab gaps --kind idiom` to look at one kind on its own.

## What to report

1. **Corpus line** - total words, how many due today, how many mastered (`mastery >=
   0.8`), how many never practised, average mastery.
2. **Active vs. passive** - from `ect vocab gaps`. Lead with the overall
   `activation_rate`, then the `by_kind` rates weakest first. This answers the question
   the due list cannot: *am I actually using any of this?* Expect idioms and phrases to
   lag single words - naming that gap is most of the value of this section. Then the
   `dormant` list: terms carried as targets that still are not being produced, with
   `times_seen` shown, because a term ignored five times is a different problem from one
   ignored once.
3. **Due now** - a compact table: term, kind, meaning, mastery, days overdue. Put the
   weakest first. Flag anything carrying a `notes` field: that is a word with a recorded
   misuse, and it is the highest-value thing to practise.
4. **Quietly strong** - two or three words with high mastery, so progress is visible.
5. **Never practised** - the `untried` bucket: terms sitting in the corpus that have
   never been carried by a session, oldest first, if there are any.
6. **One recommendation** - the single next action, usually "run `/generate-topic` and
   these 3-4 words will be pulled in automatically". Point at a dormant term over a
   merely-due one when both are available.

Write nothing. This skill never modifies the database - scheduling changes only ever
come from `/process-session`, so review state stays tied to actual practice.

If the corpus is empty, say so and point at `/generate-topic` as the way to seed it.
