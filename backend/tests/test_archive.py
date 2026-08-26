"""The recording archive (`ect archive`, ADR 0008).

Recordings are the one artifact the data repo does not hold, so these functions are the
only thing that can answer "is the local copy safe to lose?". The tests that matter most
are the ones about *not* losing it: the drop-original guard, and verify noticing a file
that changed underneath us.

Transcoding needs ffmpeg, so those tests skip without it. Everything else is pure
bookkeeping over the DB and the filesystem and runs anywhere.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app import db as dbmod
from app import services

ffmpeg_required = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def make_session(data_dir: Path, *, mode: str = "freeform", audio: bytes = b"x" * 2048):
    """A session with a recording on disk. The bytes are not real audio - only the
    transcode tests need that, and they generate a tone instead."""
    session = services.create_session(mode=mode, topic="A topic", target_words=[])
    path = data_dir / "recordings" / mode / f"{session['id']}.webm"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(audio)
    with dbmod.cursor() as conn:
        conn.execute(
            "UPDATE sessions SET audio_path = ?, duration_sec = ? WHERE id = ?",
            (services.relpath(path), 60.0, session["id"]),
        )
    return session["id"], path


def make_tone(dest: Path, seconds: int = 2) -> Path:
    """A real webm/opus file, so the transcode path is exercised on something ffmpeg
    will actually decode."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        shutil.which("ffmpeg"),
        "-v", "error", "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency=300:duration={seconds}",
        "-c:a", "libopus", "-b:a", "128k", "-ac", "1",
        str(dest),
    ]  # fmt: skip
    subprocess.run(cmd, check=True, capture_output=True)
    return dest


class TestStatus:
    def test_separates_present_from_missing_and_tracked_from_not(self, data_dir):
        """Compression is optional, so "is it here", "do we know its hash" and "is it
        compressed" are three independent facts, not one state."""
        kept, _ = make_session(data_dir)
        gone, gone_path = make_session(data_dir)
        gone_path.unlink()

        by_id = {e["session_id"]: e for e in services.archive_status()["sessions"]}
        assert by_id[kept]["state"] == "present"
        assert by_id[kept]["tracked"] is False
        assert by_id[kept]["compressed"] is False
        assert by_id[gone]["state"] == "missing"

        services.track_recordings()
        by_id = {e["session_id"]: e for e in services.archive_status()["sessions"]}
        assert by_id[kept]["tracked"] is True
        assert by_id[kept]["compressed"] is False, "tracking must not imply compressing"

    def test_a_session_with_no_audio_never_appears(self, data_dir):
        """`archive status` answers a question about files. A session that was created
        but never recorded has none, and listing it as 'missing' would be a false alarm
        on every unstarted session."""
        services.create_session(mode="freeform", topic="Never recorded", target_words=[])
        assert services.archive_status()["sessions"] == []

    def test_totals_count_only_what_is_really_on_disk(self, data_dir):
        make_session(data_dir, audio=b"y" * 4096)
        totals = services.archive_status()["totals"]
        assert totals["sessions"] == 1
        assert totals["source_bytes"] == 4096
        assert totals["uncompressed"] == 1
        assert totals["untracked"] == 1
        assert totals["reclaimable_bytes"] == 0  # nothing compressed, so nothing to reclaim


class TestTrack:
    def test_records_a_hash_without_transcoding(self, data_dir):
        """The uncompressed workflow: no ffmpeg, no archive file, but still a hash - which
        is the only thing that can notice a recording changing once it is out of git."""
        session_id, path = make_session(data_dir)
        result = services.track_recordings()

        assert [e["session_id"] for e in result["added"]] == [session_id]
        entry = services.archive_status()["sessions"][0]
        assert entry["tracked"] is True
        assert entry["compressed"] is False
        assert entry["archive_path"] is None
        assert not path.with_suffix(".opus").exists()

    def test_is_cheap_to_re_run(self, data_dir):
        session_id, _ = make_session(data_dir)
        services.track_recordings()
        again = services.track_recordings()
        assert again["unchanged"] == [session_id]
        assert again["added"] == []

    def test_rehash_notices_content_that_changed_at_the_same_size(self, data_dir):
        session_id, path = make_session(data_dir)
        services.track_recordings()
        path.write_bytes(b"q" * path.stat().st_size)

        assert services.track_recordings()["unchanged"] == [session_id], "size alone cannot see it"
        forced = services.track_recordings(rehash=True)
        assert forced["updated"][0]["content_changed"] is True

    def test_tracking_makes_verify_meaningful(self, data_dir):
        """Before tracking there is nothing to verify against - that is the whole gap
        leaving git opened."""
        _, path = make_session(data_dir)
        assert services.verify_archives()["checked"] == 0

        services.track_recordings()
        assert services.verify_archives()["ok"] is True
        path.unlink()
        assert services.verify_archives()["ok"] is False

    def test_a_deliberately_dropped_original_is_not_reported_missing(self, data_dir):
        session_id, path = make_session(data_dir)
        services.track_recordings()
        with dbmod.cursor() as conn:
            conn.execute(
                "UPDATE recording_archives SET source_present = 0 WHERE session_id = ?",
                (session_id,),
            )
        path.unlink()
        assert services.track_recordings()["missing"] == []


class TestVerify:
    def test_notices_a_file_that_changed_underneath_us(self, data_dir):
        session_id, path = make_session(data_dir)
        _fake_archive_row(session_id, path)

        assert services.verify_archives()["ok"] is True
        path.write_bytes(b"z" * 99)  # truncated / re-encoded / half-synced
        report = services.verify_archives()
        assert report["ok"] is False
        assert report["problems"][0]["problem"] == "size changed"

    def test_notices_a_file_that_vanished(self, data_dir):
        session_id, path = make_session(data_dir)
        _fake_archive_row(session_id, path)
        path.unlink()
        report = services.verify_archives()
        assert [p["problem"] for p in report["problems"]] == ["missing"]

    def test_a_hash_mismatch_needs_deep(self, data_dir):
        """Same byte count, different content - the case a size check cannot see."""
        session_id, path = make_session(data_dir)
        _fake_archive_row(session_id, path)
        path.write_bytes(b"q" * path.stat().st_size)

        assert services.verify_archives()["ok"] is True
        assert services.verify_archives(deep=True)["ok"] is False


class TestSyncBookkeeping:
    def test_marking_synced_is_explicit_and_records_where(self, data_dir):
        session_id, path = make_session(data_dir)
        _fake_archive_row(session_id, path)

        assert services.archive_status()["totals"]["unsynced"] == 1
        result = services.mark_synced([session_id], target="homeserver:/srv/ect")
        assert result["marked"] == [session_id]
        assert services.archive_status()["totals"]["unsynced"] == 0
        assert services.archive_status()["sessions"][0]["sync_target"] == "homeserver:/srv/ect"

    def test_manifest_carries_hashes_for_checking_the_far_end(self, data_dir):
        session_id, path = make_session(data_dir)
        _fake_archive_row(session_id, path)
        manifest = services.archive_manifest()
        assert manifest["count"] >= 1
        entry = next(f for f in manifest["files"] if f["kind"] == "archive")
        assert entry["sha256"] and entry["present"] is True


@ffmpeg_required
class TestCompress:
    def test_produces_a_smaller_playable_archive_and_keeps_the_original(self, data_dir):
        session_id, path = make_session(data_dir)
        make_tone(path, seconds=3)

        result = services.compress_recording(session_id, bitrate_kbps=24)
        archive = Path(services.abspath(result["archive_path"]))
        assert archive.is_file()
        assert result["archive_bytes"] < result["source_bytes"]
        assert path.is_file(), "the original must survive a compress without --drop-original"

        from pipeline.audio import duration_of

        assert duration_of(archive) is not None

    def test_is_idempotent_at_the_same_bitrate(self, data_dir):
        """Every re-encode of a lossy source loses a little more, so repeating the
        command must not silently re-do the work."""
        session_id, path = make_session(data_dir)
        make_tone(path, seconds=2)

        first = services.compress_recording(session_id, bitrate_kbps=24)
        again = services.compress_recording(session_id, bitrate_kbps=24)
        assert "skipped" in again
        assert again["archive_sha256"] == first["archive_sha256"]

    def test_a_different_bitrate_is_a_deliberate_redo(self, data_dir):
        session_id, path = make_session(data_dir)
        make_tone(path, seconds=2)

        services.compress_recording(session_id, bitrate_kbps=32)
        smaller = services.compress_recording(session_id, bitrate_kbps=16)
        assert "skipped" not in smaller
        assert smaller["bitrate_kbps"] == 16

    def test_drop_original_removes_the_source_and_records_that(self, data_dir):
        session_id, path = make_session(data_dir)
        make_tone(path, seconds=2)

        result = services.compress_recording(session_id, bitrate_kbps=24, drop_original=True)
        assert result["original_removed"] is True
        assert not path.exists()
        assert result["source_present"] == 0

        entry = services.archive_status()["sessions"][0]
        assert entry["state"] == "present"
        assert entry["compressed"] is True
        assert entry["source_present"] is False
        # A source that is deliberately gone is not a verification failure.
        assert services.verify_archives()["ok"] is True

    def test_a_dropped_source_leaves_the_manifest_honest(self, data_dir):
        session_id, path = make_session(data_dir)
        make_tone(path, seconds=2)
        services.compress_recording(session_id, bitrate_kbps=24, drop_original=True)

        kinds = [f["kind"] for f in services.archive_manifest()["files"]]
        assert kinds == ["archive"], "a deleted original must not be listed for syncing"

    def test_compress_pending_keeps_going_past_a_broken_recording(self, data_dir):
        """One unreadable file must not block the rest of the backlog."""
        broken, _ = make_session(data_dir)  # 2 KB of 'x', not decodable
        good, good_path = make_session(data_dir)
        make_tone(good_path, seconds=2)

        result = services.compress_pending(bitrate_kbps=24)
        assert [d["session_id"] for d in result["compressed"]] == [good]
        assert [f["session_id"] for f in result["failed"]] == [broken]
        assert result["saved_bytes"] > 0

    def test_a_failed_transcode_writes_no_archive_row(self, data_dir):
        """A row claiming an archive that does not exist is worse than no row: it is
        what a later --drop-original would trust."""
        session_id, _ = make_session(data_dir)  # not real audio
        with pytest.raises(RuntimeError):
            services.compress_recording(session_id)

        with dbmod.cursor() as conn:
            assert conn.execute("SELECT COUNT(*) FROM recording_archives").fetchone()[0] == 0


class TestCli:
    def test_status_prints_a_table_and_json_on_demand(self, data_dir, capsys):
        import json

        from app.cli import main

        make_session(data_dir)
        assert main(["archive", "status"]) == 0
        out = capsys.readouterr().out
        assert "original" in out
        assert "untracked" in out, "an untracked recording must say so, not look fine"

        assert main(["archive", "status", "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["totals"]["sessions"] == 1

    def test_verify_exits_nonzero_when_something_is_wrong(self, data_dir, capsys):
        from app.cli import main

        session_id, path = make_session(data_dir)
        _fake_archive_row(session_id, path)
        assert main(["archive", "verify"]) == 0
        capsys.readouterr()

        path.unlink()
        assert main(["archive", "verify"]) == 1


def _fake_archive_row(session_id: int, path: Path) -> None:
    """Record `path` as this session's archive without running ffmpeg - lets the
    bookkeeping tests run on machines without it."""
    from app.services import _sha256

    with dbmod.cursor() as conn:
        conn.execute(
            """INSERT INTO recording_archives
                   (session_id, source_path, source_bytes, source_sha256, source_present,
                    archive_path, archive_bytes, archive_sha256, bitrate_kbps, compressed_at)
               VALUES (?,?,?,?,0,?,?,?,?,?)""",
            (
                session_id,
                services.relpath(path),
                path.stat().st_size,
                _sha256(path),
                services.relpath(path),
                path.stat().st_size,
                _sha256(path),
                24,
                dbmod.utcnow(),
            ),
        )
