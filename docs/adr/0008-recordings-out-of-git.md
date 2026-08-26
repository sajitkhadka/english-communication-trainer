# ADR 0008 — Recordings leave git, and `ect archive` tracks them instead

**Status:** Accepted · **Date:** 2026-08-26 · **Interacts with:** ADR 0006

## Context

`data/` is a separate private repo (`english-communication-trainer-data`) whose README
told you to `git add -A` after every session. That worked while the tree was text. It
stopped working once recordings accumulated.

By session 23 the split was:

| | Size |
|---|---|
| recordings (`.webm`) | **102 MB** |
| everything else — DB, transcripts, feedback, profile, notes, prompts | 1.8 MB |

MediaRecorder writes Opus at a flat **129 kbps mono** regardless of content — measured
identically across all 18 recordings, because it is the browser's default rather than
anything chosen. For a single speaking voice that is roughly five times what the content
needs. 105 minutes of speech was occupying 102 MB.

Git is the wrong store for this. The files are immutable once written, so every version
is also the only version; there is no diff to compress; and a repo cannot shed history
later without a rewrite that invalidates every clone. Left alone the repo would have
passed a gigabyte within the year, and the 1.8 MB of genuinely irreplaceable text would
have been a rounding error inside it.

The complication is that dropping them from git removes the only thing that was
*tracking* them. `git status` was, in effect, the inventory. Something has to answer "is
this file still here, is it intact, and does a copy exist somewhere else?" — because the
moment the answer is a guess, deleting a local recording becomes unsafe, and the whole
point of moving them out was to be able to.

## Decision

**Three separate things, deliberately not one.**

1. **Recordings are gitignored.** `.gitignore` in the data repo excludes the audio
   extensions. The `recordings/<mode>/` directories and their `.gitkeep` files stay
   tracked, so a clone still gets the right tree shape. The data repo becomes a
   text-and-DB snapshot, and its README says so at the restore step.

2. **`ect archive track` hashes every recording and records it**, without transcoding.
   This is the load-bearing part. Leaving git removed the inventory as much as the
   storage: `git status` was what said a file was still there and still itself. The
   `recording_archives` table replaces it — source path, size and SHA-256, plus separate
   `compressed_at`, `verified_at` and `synced_at` timestamps. One row per session, written
   only by `ect archive`. A missing row means "not tracked yet", never an error. Tracking
   is cheap and idempotent: a recording whose size still matches its row is skipped, so a
   re-run costs one `stat` per session rather than re-reading 100 MB.

3. **The originals are kept. Compression is available and opt-in.**
   `ect archive compress` transcodes to mono Opus at speech bitrate
   (`pipeline.audio.transcode_opus`, `-application voip`) and measured 3.16 MB → 0.59 MB
   on session 23, **5.4×**, which would take the corpus to ~19 MB. It is not the default
   anyway, because the stated reason for keeping recordings at all is listening back to
   hear progress over months — and that is the one use a lossy-to-lossy re-encode
   degrades. The saving is real but it buys disk on a home server that has plenty, at the
   cost of the only thing the files are for.

   If it is ever used, it applies to the archive copy only: the transcript, the word
   alignment and every metric in `pipeline/metrics.py` were computed from the original and
   are never re-derived from an archive.

The commands are `status`, `track`, `compress`, `manifest`, `verify` and `synced`.

**The transfer is `rclone copy` over sftp, driven by `backup-recordings.ps1`.** `ect`
itself moves no bytes: `ect archive manifest --format sha256` emits a `sha256sum -c`
file and `ect archive synced --target` records the confirmation, and a script wires those
around the transfer. rclone rather than rsync because there is no rsync on this Windows
machine and rclone is already installed; `copy` rather than `sync` because the far end is
an archive and nothing there should ever be deleted for no longer being here.

The manifest is emitted in coreutils format on purpose: the check then runs on the server
with `sha256sum -c` and needs nothing of this project installed there.

## Interaction with ADR 0006

ADR 0006 puts a `relay/` on this same home server: a recorder PWA and an inbox, reachable
from the internet through Nginx Proxy Manager. It also says, explicitly, that inbox blobs
are **deleted on `ack`** — because the recorder is publicly addressable and `worklog`
recordings carry employer and project detail.

This ADR now puts *every* recording, permanently, on that same host. That is the opposite
posture on the same data, and the two only coexist under a condition:

- **The archive directory must not be reachable through NPM, and must not be a volume of
  the relay container.** It is reached over SSH on the LAN and nothing else. ADR 0006's
  reasoning about exposure applies to the archive with more force, not less: the inbox
  holds one recording for minutes, the archive holds all of them forever.

Two smaller points of contact, neither of which conflicts:

- ADR 0006's drain has recordings arriving *at* the PC from the relay. The archive still
  runs on the PC and still treats it as the source of truth, so the backup is simply
  something that happens after a drain rather than instead of one.
- Once `ect agent` exists (ADR 0006 rollout step 2), the backup is a natural thing to hang
  off its schedule. Nothing here depends on that — `backup-recordings.ps1` is standalone
  and ADR 0006 is still Proposed.

## Consequences

**`synced_at` is set by an explicit command, not by whatever launched a transfer.** This
is the load-bearing detail. "A copy exists off this machine" is the claim that licenses
deleting a local file, and a sync command exiting 0 does not establish it — it establishes
that a transfer was started and did not error. The confirmation is a separate step so that
it is made by whatever actually checked.

**`--drop-original` re-hashes the archive immediately before unlinking the source**, rather
than trusting the hash computed moments earlier. It is the only branch in the codebase that
destroys data, so it re-reads the file it is about to depend on. A transcode whose output
will not decode deletes its own output and raises, rather than leaving a plausible-looking
archive that a later `--drop-original` would trust. With originals kept, this branch is
currently unused — it stays guarded that way so that choosing compression later is not also
choosing to trust an unchecked file.

**Compression is idempotent per bitrate.** Re-running `compress` skips anything already
archived at that bitrate, because each re-encode of a lossy source loses a little more.
Passing a different bitrate is treated as a deliberate redo.

**A recording that is on disk but untracked is reported as untracked, not as fine.**
`archive status` prints `NO` in the tracked column and says what to run. The failure this
avoids is the quiet one: a file present, assumed backed up, with nothing anywhere that
could contradict the assumption.

**What we give up:** a `git clone` of the data repo is no longer a full restore. Recordings
must be restored from wherever they were synced. The README states this at the restore
step, since that is the only moment it matters and the only moment it would otherwise be
discovered.

## Alternatives considered

**Git LFS.** GitHub's free tier is 1 GB of LFS storage and 1 GB/month of bandwidth. At
~10 MB per session that is exhausted inside a year, after which it is a paid add-on;
and migrating *off* LFS later is worse than the problem it solves. Rejected on the same
grounds as keeping the files in git — it defers the size problem rather than removing it.

**Compress by default at 24 kbps.** The 5× is nearly free in disk terms and would make an
offsite copy cost cents a month. **Rejected**, after listening to 16 / 24 / 32 kbps samples
against the original: the recordings exist to be listened back to, and that is exactly what
lossy-on-lossy re-encoding degrades. The home server has the room. The code stays, opt-in,
for the day the corpus is large enough that the trade changes.

**Delete recordings once a session is processed.** The transcript and feedback do carry
nearly all the durable value, so this is defensible. Rejected because the stated reason for
keeping them is listening back to hear progress over months, which is exactly the use the
transcript cannot serve.

**Implement syncing inside `ect`.** Rejected above: it would be a worse rclone, and it
would tie the confirmation to the transport instead of keeping it separate.

**Syncthing instead of a scripted `rclone copy`.** Continuous, no scheduling, and it would
pick up new recordings with no run to forget. Rejected because it is bidirectional by
default and its send-only mode still expresses "make the far end match", which is the wrong
shape for an archive — a local file deleted by accident should not propagate. The scripted
copy is dumber in exactly the way that matters, and it can be scheduled if remembering to
run it becomes the problem.
