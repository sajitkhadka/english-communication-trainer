"""`ect` - the command surface the Claude Code skills drive.

Everything a skill needs to read or write lives here, so a skill is a short set of
instructions plus a handful of shell calls: no SQL in prompts, no arithmetic done by
the model, and it all works with the FastAPI server stopped.

    uv run ect --help
    uv run python -m app.cli --help   # equivalent, no console script needed
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import db as dbmod
from . import services
from .brief import brief_for_session
from .config import settings
from .paths import feedback_path

# Fields worth spending tokens on when handing vocabulary to Claude.
VOCAB_SLICE = (
    "id", "term", "kind", "meaning", "example", "due_date", "mastery",
    "times_seen", "times_used_correctly", "interval_days", "source", "notes",
)  # fmt: skip

# `ect vocab gaps` is about the usage counters, so they have to survive `slim` even at
# zero - "reviewed three times, produced none" and "never reviewed" are the two
# different findings the whole command exists to tell apart.
GAP_ALWAYS = ("term", "times_seen", "times_used_correctly")

# `--brief`: what /process-session actually needs to decide whether a dormant term had a
# slot in this session. `meaning` decides fit and `notes` says how it was missed last
# time; `example`, `due_date`, `interval_days` and `source` are SM-2 bookkeeping the
# backend acts on and the model never reads. Roughly half the tokens of the full report.
GAP_BRIEF_SLICE = ("term", "kind", "meaning", "times_seen", "times_used_correctly", "notes")


def emit(data: Any) -> None:
    if isinstance(data, str):
        print(data)
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def read_json_arg(value: str) -> Any:
    """Accept a file path, `-` for stdin, or a raw JSON string."""
    if value == "-":
        return json.loads(sys.stdin.read())
    path = Path(value)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(value)


def slim(
    row: dict[str, Any], keys: tuple[str, ...], *, always: tuple[str, ...] = ("term", "mastery")
) -> dict[str, Any]:
    """Drop empty fields to keep the JSON handed to Claude small, but always keep the
    ones that carry meaning even when zero."""
    return {k: row.get(k) for k in keys if row.get(k) not in (None, "", 0.0) or k in always}


# --------------------------------------------------------------------------- #
# db / doctor
# --------------------------------------------------------------------------- #


def cmd_db_init(args: argparse.Namespace) -> int:
    path = dbmod.init_db()
    emit({"status": "ready", "db": str(path), "data_dir": str(settings.data_dir)})
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    import shutil

    from pipeline.gpu import gpu_report

    report = gpu_report()
    report["ffmpeg"] = shutil.which("ffmpeg")
    report["db_exists"] = settings.db_path.is_file()
    report["profile_exists"] = settings.profile_path.is_file()
    report["notes_exists"] = settings.notes_path.is_file()
    report["model_cache"] = str(settings.model_cache_dir)
    emit(report)
    ok = bool(report.get("cuda_available")) and bool(report["ffmpeg"])
    return 0 if ok else 1


# --------------------------------------------------------------------------- #
# vocabulary
# --------------------------------------------------------------------------- #


def cmd_vocab_due(args: argparse.Namespace) -> int:
    with dbmod.cursor() as conn:
        rows = dbmod.due_words(conn, limit=args.limit)
        stats = dbmod.word_stats(conn)
    emit({"stats": stats, "due": [slim(r, VOCAB_SLICE) for r in rows]})
    return 0


def cmd_vocab_list(args: argparse.Namespace) -> int:
    with dbmod.cursor() as conn:
        rows = dbmod.list_words(conn, sort=args.sort, limit=args.limit)
    emit([slim(r, VOCAB_SLICE) for r in rows])
    return 0


def cmd_vocab_gaps(args: argparse.Namespace) -> int:
    """Active vs. passive corpus: what the user recognises but does not reach for."""
    with dbmod.cursor() as conn:
        report = dbmod.vocabulary_gaps(conn, limit=args.limit, kind=args.kind)
    keys = GAP_BRIEF_SLICE if args.brief else VOCAB_SLICE
    for bucket in dbmod.GAP_BUCKETS:
        report[bucket] = [slim(r, keys, always=GAP_ALWAYS) for r in report[bucket]]
    emit(report)
    return 0


def cmd_vocab_stats(args: argparse.Namespace) -> int:
    with dbmod.cursor() as conn:
        emit(dbmod.word_stats(conn))
    return 0


def cmd_vocab_add(args: argparse.Namespace) -> int:
    """Bulk-add vocabulary: [{"term","kind","meaning","example","source"}, ...]"""
    payload = read_json_arg(args.json)
    items = payload if isinstance(payload, list) else payload.get("words", [])
    added: list[str] = []
    with dbmod.cursor() as conn:
        for item in items:
            term = str(item.get("term", "")).strip()
            if not term:
                continue
            dbmod.add_word(
                conn,
                term=term,
                kind=item.get("kind"),
                meaning=item.get("meaning"),
                example=item.get("example"),
                source=item.get("source") or "recommended",
                notes=item.get("note") or item.get("notes"),
            )
            added.append(term)
    emit({"added": added, "count": len(added)})
    return 0


# --------------------------------------------------------------------------- #
# sessions
# --------------------------------------------------------------------------- #

SESSION_SLICE = (
    "id", "mode", "category", "topic", "target_words", "status", "duration_sec",
    "transcribe_status", "transcribe_error", "created_at", "processed_at",
)  # fmt: skip


def cmd_session_create(args: argparse.Namespace) -> int:
    targets = [t.strip() for t in (args.target_words or "").split(",") if t.strip()]
    try:
        session = services.create_session(
            mode=args.mode,
            topic=args.topic,
            category=args.category,
            target_words=targets,
            notes=args.notes,
        )
    except services.WorkflowError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    emit(
        {
            "session_id": session["id"],
            "status": session["status"],
            "mode": session["mode"],
            "topic": session["topic"],
            "target_words": session["target_words"],
            "prompt_file": str(services.prompt_path(session["id"])),
        }
    )
    return 0


def cmd_session_list(args: argparse.Namespace) -> int:
    with dbmod.cursor() as conn:
        rows = dbmod.list_sessions(conn, mode=args.mode, status=args.status, limit=args.limit)
        out = []
        for row in rows:
            item = {k: row.get(k) for k in SESSION_SLICE}
            item["score"] = (dbmod.get_score(conn, row["id"]) or {}).get("overall")
            out.append(item)
    emit(out)
    return 0


def cmd_session_show(args: argparse.Namespace) -> int:
    with dbmod.cursor() as conn:
        session = dbmod.get_session(conn, args.session_id)
        if session is None:
            print(f"error: session {args.session_id} not found", file=sys.stderr)
            return 2
        session["score"] = dbmod.get_score(conn, args.session_id)
        session["words"] = dbmod.words_for_session(conn, args.session_id)
    emit(session)
    return 0


def cmd_session_brief(args: argparse.Namespace) -> int:
    try:
        emit(brief_for_session(args.session_id, max_sentences=args.max_sentences))
    except (LookupError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_session_transcribe(args: argparse.Namespace) -> int:
    try:
        emit(services.transcribe_session(args.session_id, force=args.force))
    except services.WorkflowError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"error: transcription failed: {exc}", file=sys.stderr)
        return 3
    return 0


def cmd_session_attach(args: argparse.Namespace) -> int:
    """Attach an audio file recorded outside the browser (phone memo, headset, etc.)."""
    path = Path(args.audio)
    if not path.is_file():
        print(f"error: audio file not found: {path}", file=sys.stderr)
        return 2
    try:
        session = services.store_recording(
            args.session_id, path.read_bytes(), suffix=path.suffix or ".wav"
        )
    except services.WorkflowError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    emit(
        {
            "session_id": session["id"],
            "status": session["status"],
            "audio_path": session["audio_path"],
            "duration_sec": session["duration_sec"],
        }
    )
    return 0


def cmd_session_pending(args: argparse.Namespace) -> int:
    """What `/process-session` with no argument should work through."""
    sessions = services.pending_sessions()
    out = []
    for session in sessions:
        item = {k: session.get(k) for k in SESSION_SLICE}
        item["has_transcript"] = services.transcript_path(session["id"]).is_file()
        out.append(item)
    emit({"count": len(out), "pending": out})
    return 0


def cmd_session_enqueue(args: argparse.Namespace) -> int:
    try:
        emit(services.enqueue(args.session_id))
    except services.WorkflowError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_session_set_mode(args: argparse.Namespace) -> int:
    try:
        emit(services.change_session_mode(args.session_id, args.mode))
    except services.WorkflowError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


# --------------------------------------------------------------------------- #
# feedback write-back
# --------------------------------------------------------------------------- #


def cmd_feedback_apply(args: argparse.Namespace) -> int:
    payload = read_json_arg(args.json)
    markdown = None
    if args.markdown:
        md_path = Path(args.markdown)
        if not md_path.is_file():
            print(f"error: markdown file not found: {md_path}", file=sys.stderr)
            return 2
        markdown = md_path.read_text(encoding="utf-8")
    else:
        # Without `--markdown` the only markdown that can be linked is a file already
        # sitting at the canonical path. A skill that wrote `data/feedback/<id>.md`
        # relative to `backend/` misses it, and the session would be marked processed
        # with no feedback for the frontend to render - a silent loss discovered only
        # by looking at the UI. Refuse instead, and say where the file has to be.
        session_id = payload.get("session_id") if isinstance(payload, dict) else None
        expected = feedback_path(int(session_id)) if session_id is not None else None
        if expected is None or not expected.is_file():
            print(
                f"error: no feedback markdown for session {session_id}. Pass --markdown "
                f"<path>, or write the file to {expected} first.",
                file=sys.stderr,
            )
            return 2
    try:
        emit(services.record_feedback(payload, markdown=markdown))
    except services.WorkflowError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


# --------------------------------------------------------------------------- #
# worklog
# --------------------------------------------------------------------------- #

WORKLOG_SLICE = ("entry_date", "projects", "tags", "summary", "path", "session_id")


def _csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def cmd_worklog_add(args: argparse.Namespace) -> int:
    md_path = Path(args.markdown)
    if not md_path.is_file():
        print(f"error: markdown file not found: {md_path}", file=sys.stderr)
        return 2
    try:
        emit(
            services.record_worklog_entry(
                entry_date=args.date,
                markdown=md_path.read_text(encoding="utf-8"),
                summary=args.summary,
                title=args.title,
                projects=_csv(args.projects),
                tags=_csv(args.tags),
                session_id=args.session,
            )
        )
    except services.WorkflowError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_worklog_list(args: argparse.Namespace) -> int:
    with dbmod.cursor() as conn:
        rows = dbmod.list_worklog_entries(
            conn,
            month=args.month,
            date_from=getattr(args, "from"),
            date_to=args.to,
            tag=args.tag,
            project=args.project,
            limit=args.limit,
        )
    emit([{k: r.get(k) for k in WORKLOG_SLICE} for r in rows])
    return 0


def cmd_worklog_show(args: argparse.Namespace) -> int:
    """Print a daily entry (YYYY-MM-DD) or a monthly rollup (YYYY-MM)."""
    from .paths import abspath, worklog_rollup_path

    ref = args.date_or_month
    if len(ref) > 7:
        # The filename carries a title slug, so it is no longer guessable from the
        # date alone - the DB row is the source of truth for where it lives.
        with dbmod.cursor() as conn:
            entry = dbmod.get_worklog_entry(conn, ref)
        path = abspath(entry["path"]) if entry else None
    else:
        path = worklog_rollup_path(ref)
    if path is None or not path.is_file():
        kind = "entry" if len(ref) > 7 else "rollup"
        print(f"error: no worklog {kind} for {ref}", file=sys.stderr)
        return 2
    emit(path.read_text(encoding="utf-8"))
    return 0


def cmd_worklog_rollup_add(args: argparse.Namespace) -> int:
    md_path = Path(args.markdown)
    if not md_path.is_file():
        print(f"error: markdown file not found: {md_path}", file=sys.stderr)
        return 2
    try:
        emit(
            services.record_worklog_rollup(
                month=args.month, markdown=md_path.read_text(encoding="utf-8")
            )
        )
    except services.WorkflowError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_worklog_rollup_status(args: argparse.Namespace) -> int:
    emit(services.worklog_rollup_status())
    return 0


# --------------------------------------------------------------------------- #
# brainstorm
# --------------------------------------------------------------------------- #


def cmd_brainstorm_add(args: argparse.Namespace) -> int:
    md_path = Path(args.markdown)
    if not md_path.is_file():
        print(f"error: markdown file not found: {md_path}", file=sys.stderr)
        return 2
    try:
        emit(
            services.record_brainstorm_entry(
                session_id=args.session,
                markdown=md_path.read_text(encoding="utf-8"),
                title=args.title,
                summary=args.summary,
            )
        )
    except services.WorkflowError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


# --------------------------------------------------------------------------- #
# suggestion requests
# --------------------------------------------------------------------------- #


def cmd_requests_list(args: argparse.Namespace) -> int:
    with dbmod.cursor() as conn:
        emit(dbmod.open_suggestion_requests(conn))
    return 0


def cmd_requests_close(args: argparse.Namespace) -> int:
    with dbmod.cursor() as conn:
        conn.execute(
            "UPDATE suggestion_requests SET status = 'fulfilled' WHERE id = ?",
            (args.request_id,),
        )
    emit({"id": args.request_id, "status": "fulfilled"})
    return 0


def cmd_suggest_add(args: argparse.Namespace) -> int:
    payload = read_json_arg(args.json)
    items = payload if isinstance(payload, list) else payload.get("suggestions", [])
    ids = []
    with dbmod.cursor() as conn:
        for item in items:
            if not item.get("topic"):
                continue
            ids.append(
                dbmod.add_suggestion(
                    conn,
                    mode=item.get("mode") or "recommended",
                    topic=item["topic"],
                    category=item.get("category"),
                    target_words=item.get("target_words") or [],
                    rationale=item.get("rationale"),
                )
            )
    emit({"added": ids, "count": len(ids)})
    return 0


# --------------------------------------------------------------------------- #
# agent (ADR 0006)
# --------------------------------------------------------------------------- #


def _agent_or_exit() -> Any:
    from .agent import Agent, AgentError

    try:
        return Agent()
    except AgentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def cmd_agent_run(args: argparse.Namespace) -> int:
    import logging

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    with _agent_or_exit() as agent:
        try:
            agent.run_forever()
        except KeyboardInterrupt:
            print("agent stopped", file=sys.stderr)
    return 0


def cmd_agent_once(args: argparse.Namespace) -> int:
    """A single pass. What to run when remote capture 'did not arrive'."""
    with _agent_or_exit() as agent:
        report: dict[str, Any] = {"heartbeat": agent.heartbeat()}
        report.update(agent.drain().as_dict())
        if not args.no_digest:
            report["digest"] = agent.push_digest(force=args.force_digest)
    emit(report)
    return 0


def cmd_agent_status(args: argparse.Namespace) -> int:
    from .agent import status

    report = status()
    emit(report)
    return 0 if report.get("ok") else 1


def cmd_agent_digest(args: argparse.Namespace) -> int:
    """Print the snapshot itself - what the relay would serve while the PC is off."""
    from .digest import build_digest

    payload = build_digest(feedback_horizon=args.horizon)
    if args.summary:
        emit(
            {
                "version": payload["version"],
                "generated_at": payload["generated_at"],
                "feedback_horizon": payload["feedback_horizon"],
                "sessions": len(payload["sessions"]),
                "with_feedback": sum(
                    1 for d in payload["session_details"].values() if d.get("feedback_markdown")
                ),
                "words": len(payload["words"]),
                "bytes": len(json.dumps(payload, default=str)),
            }
        )
    else:
        emit(payload)
    return 0


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #


def _mb(n: int | None) -> str:
    return f"{(n or 0) / 1e6:.1f} MB"


def cmd_archive_status(args: argparse.Namespace) -> int:
    """What is on this machine, what is tracked, and what is confirmed off it."""
    report = services.archive_status()
    if args.json:
        emit(report)
        return 0
    t = report["totals"]
    print(f"{'id':>4}  {'mode':<11} {'size':>9}  {'tracked':<8} {'copy':<9} synced")
    for e in report["sessions"]:
        synced = (e["synced_at"] or "")[:10] or "-"
        size = _mb(e["source_bytes"]) if e["source_present"] else "(gone)"
        copy = "archive" if e["compressed"] else ("original" if e["source_present"] else "-")
        tracked = "yes" if e["tracked"] else "NO"
        print(f"{e['session_id']:>4}  {e['mode']:<11} {size:>9}  {tracked:<8} {copy:<9} {synced}")
    print(
        f"\n{t['sessions']} recordings | {_mb(t['source_bytes'])} on disk | "
        f"{t['untracked']} untracked | {t['unsynced']} not confirmed off this machine"
    )
    if t["missing"]:
        print(f"{t['missing']} recording(s) are gone from disk - run `ect archive verify`.")
    if t["untracked"]:
        print(
            "Run `ect archive track` to hash and record them - that is what makes an\n"
            "altered or missing file detectable now the audio is not in git."
        )
    if t["reclaimable_bytes"]:
        print(
            f"{_mb(t['reclaimable_bytes'])} of originals have a compressed archive and "
            f"could be dropped - only once `synced` is set."
        )
    return 0


def cmd_archive_track(args: argparse.Namespace) -> int:
    """Hash the recordings and record them, without transcoding anything."""
    emit(services.track_recordings(rehash=args.rehash))
    return 0


def cmd_archive_compress(args: argparse.Namespace) -> int:
    if args.session:
        emit(
            services.compress_recording(
                args.session, bitrate_kbps=args.bitrate, drop_original=args.drop_original
            )
        )
        return 0
    result = services.compress_pending(
        bitrate_kbps=args.bitrate, drop_original=args.drop_original, limit=args.limit
    )
    emit(result)
    return 1 if result["failed"] else 0


def cmd_archive_manifest(args: argparse.Namespace) -> int:
    manifest = services.archive_manifest()
    if args.format == "sha256":
        # `sha256sum -c` format, deliberately: the check then runs with coreutils on the
        # far end and needs nothing of this project installed there.
        root = "data/recordings/"
        lines = [
            f"{f['sha256']}  {f['path'][len(root) :] if f['path'].startswith(root) else f['path']}"
            for f in manifest["files"]
            if f["sha256"]
        ]
        body = "\n".join(lines) + ("\n" if lines else "")
    else:
        body = json.dumps(manifest, indent=2, ensure_ascii=False)

    if args.out:
        # newline="\n" explicitly. On Windows the default translates it to CRLF, and
        # `sha256sum -c` on the server then looks for a filename with a trailing CR
        # and reports every file missing. The manifest is written on whichever machine
        # holds the recordings and read on the server, so it is always written for the
        # reader.
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
        print(f"{manifest['count']} files -> {args.out}")
        return 0
    print(body, end="" if args.format == "sha256" else "\n")
    return 0


def cmd_archive_verify(args: argparse.Namespace) -> int:
    report = services.verify_archives(deep=args.deep)
    emit(report)
    return 0 if report["ok"] else 1


def cmd_archive_synced(args: argparse.Namespace) -> int:
    emit(services.mark_synced(args.session or None, target=args.target))
    return 0


def build_parser() -> argparse.ArgumentParser:  # noqa: PLR0915 - flat argparse setup
    parser = argparse.ArgumentParser(
        prog="ect",
        description="English Communication Trainer - backend command surface for skills.",
    )
    sub = parser.add_subparsers(dest="group", required=True)

    # db
    db_p = sub.add_parser("db", help="database maintenance")
    db_sub = db_p.add_subparsers(dest="action", required=True)
    db_sub.add_parser("init", help="create the schema and data/ tree (idempotent)").set_defaults(
        func=cmd_db_init
    )

    sub.add_parser("doctor", help="check GPU / ffmpeg / db readiness").set_defaults(func=cmd_doctor)

    # vocab
    vocab_p = sub.add_parser("vocab", help="vocabulary corpus")
    vocab_sub = vocab_p.add_subparsers(dest="action", required=True)
    p = vocab_sub.add_parser("due", help="words due for review, least mastered first")
    p.add_argument("--limit", type=int, default=15)
    p.set_defaults(func=cmd_vocab_due)
    p = vocab_sub.add_parser("list", help="the whole corpus")
    p.add_argument(
        "--sort", default="recency", choices=["recency", "frequency", "mastery", "alpha", "due"]
    )
    p.add_argument("--limit", type=int, default=1000)
    p.set_defaults(func=cmd_vocab_list)
    p = vocab_sub.add_parser(
        "gaps", help="active vs. passive: terms recognised but never reached for"
    )
    p.add_argument("--limit", type=int, default=30, help="max terms per bucket")
    p.add_argument("--kind", choices=["word", "phrase", "idiom"])
    p.add_argument(
        "--brief",
        action="store_true",
        help="drop the SM-2 bookkeeping and example sentences; keeps term, kind, "
        "meaning, notes and the usage counters. Use this when handing gaps to Claude.",
    )
    p.set_defaults(func=cmd_vocab_gaps)
    vocab_sub.add_parser("stats", help="corpus totals").set_defaults(func=cmd_vocab_stats)
    p = vocab_sub.add_parser("add", help="bulk-add words from JSON")
    p.add_argument("--json", required=True, help="file path, '-' for stdin, or a JSON string")
    p.set_defaults(func=cmd_vocab_add)

    # sessions
    sess_p = sub.add_parser("session", help="practice sessions")
    sess_sub = sess_p.add_subparsers(dest="action", required=True)

    p = sess_sub.add_parser("create", help="create a session + prompt file")
    p.add_argument(
        "--mode",
        required=True,
        choices=["recommended", "freeform", "interview", "worklog", "brainstorm", "journal"],
    )
    p.add_argument("--topic", help="topic, or the interview question")
    p.add_argument("--category")
    p.add_argument("--target-words", help="comma-separated terms")
    p.add_argument("--notes")
    p.set_defaults(func=cmd_session_create)

    p = sess_sub.add_parser("list", help="list sessions")
    p.add_argument(
        "--mode",
        choices=["recommended", "freeform", "interview", "worklog", "brainstorm", "journal"],
    )
    p.add_argument("--status", choices=["awaiting_recording", "recorded", "pending", "processed"])
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_session_list)

    p = sess_sub.add_parser("show", help="full row for one session")
    p.add_argument("session_id", type=int)
    p.set_defaults(func=cmd_session_show)

    p = sess_sub.add_parser(
        "brief", help="annotated, token-cheap view of a recording (read this, not the raw JSON)"
    )
    p.add_argument("session_id", type=int)
    p.add_argument("--max-sentences", type=int, default=None)
    p.set_defaults(func=cmd_session_brief)

    p = sess_sub.add_parser("transcribe", help="run WhisperX + Silero on a recording")
    p.add_argument("session_id", type=int)
    p.add_argument("--force", action="store_true", help="re-transcribe even if a transcript exists")
    p.set_defaults(func=cmd_session_transcribe)

    p = sess_sub.add_parser("attach", help="attach an audio file recorded outside the browser")
    p.add_argument("session_id", type=int)
    p.add_argument("--audio", required=True, help="path to a wav/mp3/m4a/webm file")
    p.set_defaults(func=cmd_session_attach)

    sess_sub.add_parser("pending", help="sessions flagged for Claude").set_defaults(
        func=cmd_session_pending
    )

    p = sess_sub.add_parser("enqueue", help="flag a session pending (what the UI button does)")
    p.add_argument("session_id", type=int)
    p.set_defaults(func=cmd_session_enqueue)

    p = sess_sub.add_parser(
        "set-mode", help="change a session's mode before deciding how to process it"
    )
    p.add_argument("session_id", type=int)
    p.add_argument("mode", choices=list(services.SWITCHABLE_MODES))
    p.set_defaults(func=cmd_session_set_mode)

    # feedback
    fb_p = sub.add_parser("feedback", help="write analysis back to the DB")
    fb_sub = fb_p.add_subparsers(dest="action", required=True)
    p = fb_sub.add_parser(
        "apply",
        help="apply one session's feedback: scores, SM-2 updates, new words, status",
    )
    p.add_argument("--json", required=True, help="file path, '-' for stdin, or a JSON string")
    p.add_argument("--markdown", help="path to the feedback markdown to store")
    p.set_defaults(func=cmd_feedback_apply)

    # worklog
    wl_p = sub.add_parser("worklog", help="daily work journal (PRD-worklog)")
    wl_sub = wl_p.add_subparsers(dest="action", required=True)

    p = wl_sub.add_parser("add", help="file one day's entry: markdown + index row")
    p.add_argument("--markdown", required=True, help="path to the entry markdown")
    p.add_argument("--date", required=True, help="ISO date the entry is for (YYYY-MM-DD)")
    p.add_argument("--summary", required=True, help="one line, shown in listings")
    p.add_argument(
        "--title", help="short, filename-safe title - shown in the UI, used in the filename"
    )
    p.add_argument("--projects", help="comma-separated project slugs")
    p.add_argument("--tags", help=f"comma-separated, from: {', '.join(services.WORKLOG_TAGS)}")
    p.add_argument("--session", type=int, help="worklog session to link and mark processed")
    p.set_defaults(func=cmd_worklog_add)

    p = wl_sub.add_parser("list", help="index rows: date, projects, tags, summary, path")
    p.add_argument("--month", help="YYYY-MM")
    p.add_argument("--from", dest="from", help="ISO date lower bound")
    p.add_argument("--to", help="ISO date upper bound")
    p.add_argument("--tag", choices=list(services.WORKLOG_TAGS))
    p.add_argument("--project")
    p.add_argument("--limit", type=int, default=200)
    p.set_defaults(func=cmd_worklog_list)

    p = wl_sub.add_parser("show", help="print a daily entry (YYYY-MM-DD) or rollup (YYYY-MM)")
    p.add_argument("date_or_month")
    p.set_defaults(func=cmd_worklog_show)

    rollup_p = wl_sub.add_parser("rollup", help="monthly rollups")
    rollup_sub = rollup_p.add_subparsers(dest="rollup_action", required=True)
    p = rollup_sub.add_parser("add", help="file a month's rollup (overwrites: it is derived)")
    p.add_argument("--month", required=True, help="YYYY-MM")
    p.add_argument("--markdown", required=True, help="path to the rollup markdown")
    p.set_defaults(func=cmd_worklog_rollup_add)
    rollup_sub.add_parser("status", help="completed months lacking a rollup").set_defaults(
        func=cmd_worklog_rollup_status
    )

    # brainstorm
    bs_p = sub.add_parser("brainstorm", help="idea-dump sessions: no coaching, no score")
    bs_sub = bs_p.add_subparsers(dest="action", required=True)
    p = bs_sub.add_parser("add", help="file one session's ideas: markdown + status flip")
    p.add_argument("--session", type=int, required=True, help="brainstorm session to link")
    p.add_argument("--markdown", required=True, help="path to the ideas markdown")
    p.add_argument(
        "--title", help="short, filename-safe title - shown in the UI, used in the filename"
    )
    p.add_argument("--summary", help="one line, shown in the UI")
    p.set_defaults(func=cmd_brainstorm_add)

    # suggestions
    req_p = sub.add_parser("requests", help="frontend requests for topic suggestions")
    req_sub = req_p.add_subparsers(dest="action", required=True)
    req_sub.add_parser("list", help="open requests").set_defaults(func=cmd_requests_list)
    p = req_sub.add_parser("close", help="mark a request fulfilled")
    p.add_argument("request_id", type=int)
    p.set_defaults(func=cmd_requests_close)

    sug_p = sub.add_parser("suggest", help="topic suggestions shown on the Suggestions page")
    sug_sub = sug_p.add_subparsers(dest="action", required=True)
    p = sug_sub.add_parser("add", help="add suggestions from JSON")
    p.add_argument("--json", required=True)
    p.set_defaults(func=cmd_suggest_add)

    # --- archive -----------------------------------------------------------------
    arch_p = sub.add_parser("archive", help="recording archive: compress, track, verify")
    arch_sub = arch_p.add_subparsers(dest="action", required=True)

    p = arch_sub.add_parser("status", help="what is compressed, and what is off this machine")
    p.add_argument("--json", action="store_true", help="machine-readable instead of a table")
    p.set_defaults(func=cmd_archive_status)

    p = arch_sub.add_parser("compress", help="transcode recordings to low-bitrate Opus")
    p.add_argument("--session", type=int, help="one session; default is every uncompressed one")
    p.add_argument(
        "--bitrate", type=int, default=24, help="kbps, mono Opus (default 24; 16 is smaller)"
    )
    p.add_argument("--limit", type=int, help="stop after this many, for a first run")
    p.add_argument(
        "--drop-original",
        action="store_true",
        help="delete the source after the archive verifies. Only with a confirmed "
        "off-machine copy.",
    )
    p.set_defaults(func=cmd_archive_compress)

    p = arch_sub.add_parser("track", help="hash and record recordings (no transcoding)")
    p.add_argument(
        "--rehash",
        action="store_true",
        help="re-read every file instead of trusting an unchanged size",
    )
    p.set_defaults(func=cmd_archive_track)

    p = arch_sub.add_parser("manifest", help="the file list + hashes a sync tool should move")
    p.add_argument("--out", help="write here instead of stdout")
    p.add_argument(
        "--format",
        choices=["json", "sha256"],
        default="json",
        help="sha256 emits `sha256sum -c` format, checkable on the server with coreutils",
    )
    p.set_defaults(func=cmd_archive_manifest)

    p = arch_sub.add_parser("verify", help="re-check disk against what was recorded")
    p.add_argument("--deep", action="store_true", help="re-hash, not just size (slow)")
    p.set_defaults(func=cmd_archive_verify)

    p = arch_sub.add_parser("synced", help="record that a copy is confirmed off this machine")
    p.add_argument("--session", type=int, nargs="*", help="default is all archived sessions")
    p.add_argument(
        "--target", required=True, help="where it was confirmed, e.g. 'homeserver:/srv/ect'"
    )
    p.set_defaults(func=cmd_archive_synced)

    # agent (ADR 0006) - remote capture drain, heartbeat, digest push
    ag_p = sub.add_parser("agent", help="drain the relay inbox and mirror the digest")
    ag_sub = ag_p.add_subparsers(dest="action", required=True)

    p = ag_sub.add_parser("run", help="the loop; what the scheduled task starts at logon")
    p.add_argument("--verbose", action="store_true", help="debug logging")
    p.set_defaults(func=cmd_agent_run)

    p = ag_sub.add_parser("once", help="a single drain + digest pass, then exit")
    p.add_argument("--no-digest", action="store_true", help="drain only")
    p.add_argument(
        "--force-digest",
        action="store_true",
        help="push the snapshot even if its version has not changed",
    )
    p.set_defaults(func=cmd_agent_once)

    ag_sub.add_parser(
        "status", help="can the agent reach the relay and the local API?"
    ).set_defaults(func=cmd_agent_status)

    p = ag_sub.add_parser("digest", help="build the offline snapshot and print it")
    p.add_argument("--summary", action="store_true", help="sizes and counts, not the payload")
    p.add_argument("--horizon", type=int, help="override digest_feedback_horizon")
    p.set_defaults(func=cmd_agent_digest)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    dbmod.init_db()  # cheap and idempotent; means no command can fail on a fresh clone
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
