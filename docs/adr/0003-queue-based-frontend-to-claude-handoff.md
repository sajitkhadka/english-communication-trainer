# ADR 0003 — Queue-based frontend → Claude handoff

**Status:** Accepted · **Date:** 2026-07-30 · **PRD:** §6.3

## Context

Feedback generation is the one step that needs a language model. The frontend is where
the user is standing when they finish recording, so the obvious design is a "Get
feedback" button that calls Claude and renders the result.

Claude Code is not a service a web page can call. The routes that would make it callable
each cost something the project has explicitly decided not to spend.

## Decision

**The frontend expresses intent; the user pulls the trigger.**

- Pressing **Process** sets the session's `status` to `pending` (and drops a marker in
  `data/queue/` as a filesystem-visible mirror).
- Transcription — GPU work with no model involvement — runs at that moment, so the
  transcript is ready and waiting.
- The user runs `/process-session` in the Claude Code console. With no argument it drains
  every `pending` session, writes feedback, scores, and vocabulary updates, and flips each
  to `processed`.
- The frontend polls `GET /api/queue` and shows what is waiting.

## Alternatives

**Claude Agent SDK / headless invocation from the backend.** Technically clean, and it
would make the button do what the button appears to do. It is also the cloud API the PRD
rules out (Non-Goals §2): it bills tokens per press and moves control of when the model
runs away from the user. Rejected on both counts.

**A local model for feedback.** The GPU is already holding `large-v3` in 6 GB, and a local
model small enough to fit alongside it would not produce feedback at the quality this tool
exists to provide. The whole architecture is built on the split that the backend does the
deterministic heavy lifting *precisely so* the model doing judgement can be a good one.

**No queue — the user just tells Claude which session to process.** Works, and remains
available (`/process-session 12`). But after a batch of three recordings the user has to
remember which ones they wanted analysed, which is state the app should hold.

## Consequences

- Zero API tokens are spent without the user typing a command. Nothing runs unattended.
- Batching falls out for free: record three sessions, press Process three times, run
  `/process-session` once.
- The cost is one manual step per batch, and a UI that must be honest about it — the
  Process button says the session is queued for Claude, not that feedback is coming. The
  API's `ProcessResponse` carries that hint text so the frontend cannot drift from it.
- `status` is the single source of truth; the `data/queue/` markers are a convenience for
  inspecting state from a shell, and nothing reads them to make decisions.
- If this ever becomes annoying enough to revisit, the change is contained: the queue and
  the write-back contract (`ect feedback apply`) would not move, only what invokes them.
