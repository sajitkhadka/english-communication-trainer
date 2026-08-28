"""Active vs. passive vocabulary (`ect vocab gaps`).

The split is arithmetic over columns `record_feedback` already maintains, so it is
testable without a GPU, a transcript, or Claude - same reasoning as `test_metrics.py`.
"""

from __future__ import annotations

import pytest

from app import db as dbmod


def seed(conn, term, *, kind="word", seen=0, correct=0, source="recommended", first_seen=None):
    word_id = dbmod.add_word(conn, term=term, kind=kind, source=source)
    conn.execute(
        "UPDATE words SET times_seen = ?, times_used_correctly = ?, first_seen = ? WHERE id = ?",
        (seen, correct, first_seen or dbmod.today(), word_id),
    )
    return word_id


class TestBuckets:
    def test_reviewed_but_never_produced_is_dormant(self, conn):
        seed(conn, "de-risk", seen=3, correct=0)
        report = dbmod.vocabulary_gaps(conn)
        assert [w["term"] for w in report["dormant"]] == ["de-risk"]
        assert report["stats"]["active"] == 0

    def test_never_reviewed_is_untried_not_dormant(self, conn):
        """The distinction the command exists for: a word nobody has asked for yet is
        not evidence of a gap, and lumping the two together buries the real finding."""
        seed(conn, "circle back", kind="phrase", seen=0, correct=0)
        report = dbmod.vocabulary_gaps(conn)
        assert [w["term"] for w in report["untried"]] == ["circle back"]
        assert report["dormant"] == []

    def test_majority_correct_is_active(self, conn):
        seed(conn, "bottleneck", seen=2, correct=1)
        report = dbmod.vocabulary_gaps(conn)
        assert report["stats"]["active"] == 1
        assert report["dormant"] == [] and report["shaky"] == []

    def test_produced_sometimes_is_shaky(self, conn):
        seed(conn, "mitigate", seen=4, correct=1)
        report = dbmod.vocabulary_gaps(conn)
        assert [w["term"] for w in report["shaky"]] == ["mitigate"]

    def test_user_speech_counts_as_active_with_no_reviews(self, conn):
        """It is in the corpus because they reached for it unprompted - which is the
        definition of active vocabulary, review history or not."""
        seed(conn, "hand-wavy", seen=0, correct=0, source="user_speech")
        report = dbmod.vocabulary_gaps(conn)
        assert report["stats"]["active"] == 1
        assert report["untried"] == []


class TestReport:
    def test_activation_rate_and_empty_corpus(self, conn):
        assert dbmod.vocabulary_gaps(conn)["stats"] == {
            "total": 0,
            "active": 0,
            "dormant": 0,
            "shaky": 0,
            "untried": 0,
            "activation_rate": 0.0,
        }
        seed(conn, "leverage", seen=2, correct=2)
        seed(conn, "de-risk", seen=2, correct=0)
        assert dbmod.vocabulary_gaps(conn)["stats"]["activation_rate"] == 0.5

    def test_by_kind_puts_the_weakest_kind_first(self, conn):
        seed(conn, "leverage", kind="word", seen=2, correct=2)
        seed(conn, "throughput", kind="word", seen=2, correct=2)
        seed(conn, "move the needle", kind="idiom", seen=3, correct=0)
        report = dbmod.vocabulary_gaps(conn)
        assert [k["kind"] for k in report["by_kind"]] == ["idiom", "word"]
        assert report["by_kind"][0]["activation_rate"] == 0.0
        assert report["by_kind"][1]["activation_rate"] == 1.0

    def test_dormant_is_ordered_by_chances_missed(self, conn):
        seed(conn, "once", seen=1, correct=0)
        seed(conn, "five times", seen=5, correct=0)
        seed(conn, "twice", seen=2, correct=0)
        report = dbmod.vocabulary_gaps(conn)
        assert [w["term"] for w in report["dormant"]] == ["five times", "twice", "once"]

    def test_untried_is_ordered_oldest_first(self, conn):
        seed(conn, "newer", first_seen="2026-03-01")
        seed(conn, "older", first_seen="2026-01-01")
        report = dbmod.vocabulary_gaps(conn)
        assert [w["term"] for w in report["untried"]] == ["older", "newer"]

    def test_kind_filter_narrows_the_whole_report(self, conn):
        seed(conn, "leverage", kind="word", seen=2, correct=2)
        seed(conn, "move the needle", kind="idiom", seen=3, correct=0)
        report = dbmod.vocabulary_gaps(conn, kind="idiom")
        assert report["stats"]["total"] == 1
        assert [k["kind"] for k in report["by_kind"]] == ["idiom"]

    def test_limit_caps_each_bucket(self, conn):
        for i in range(5):
            seed(conn, f"dormant-{i}", seen=2, correct=0)
        report = dbmod.vocabulary_gaps(conn, limit=2)
        assert len(report["dormant"]) == 2
        assert report["stats"]["dormant"] == 5  # the count is of the whole bucket


class TestThroughTheFeedbackPath:
    def test_a_target_word_left_unused_becomes_dormant(self, data_dir):
        """End-to-end over the real write path: `record_feedback` is what moves the
        counters, so the bucketing has to agree with what it writes."""
        from app import services

        session = services.create_session(
            mode="recommended", topic="A migration", target_words=["de-risk", "bottleneck"]
        )
        services.record_feedback(
            {
                "session_id": session["id"],
                "scores": {"vocab_range": 6.0, "fluency": 6.0},
                "target_words": [
                    {"term": "de-risk", "used": False, "used_correctly": False},
                    {"term": "bottleneck", "used": True, "used_correctly": True},
                ],
            }
        )
        with dbmod.cursor() as conn:
            report = dbmod.vocabulary_gaps(conn)
        assert [w["term"] for w in report["dormant"]] == ["de-risk"]
        assert report["stats"]["active"] == 1


class TestCli:
    def test_gaps_command_keeps_zero_counters(self, data_dir, capsys):
        """`slim` drops falsy fields, but "seen 2, produced 0" is the entire finding -
        dropping the zero would leave it indistinguishable from a never-reviewed term."""
        import json

        from app.cli import main

        with dbmod.cursor() as conn:
            seed(conn, "de-risk", seen=2, correct=0)
        assert main(["vocab", "gaps"]) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["dormant"][0]["times_used_correctly"] == 0
        assert report["dormant"][0]["times_seen"] == 2

    def test_brief_drops_sm2_bookkeeping_but_keeps_the_judgement_fields(self, data_dir, capsys):
        """`--brief` exists to cut tokens on the /process-session path. It may only drop
        fields the model never reads - `meaning` decides whether a term fitted the
        session and `notes` says how it was missed last time, so both have to survive."""
        import json

        from app.cli import main

        with dbmod.cursor() as conn:
            word_id = seed(conn, "de-risk", seen=2, correct=0)
            conn.execute(
                "UPDATE words SET meaning = ?, example = ?, notes = ? WHERE id = ?",
                ("reduce the risk of a change", "We de-risked the migration.", "misused", word_id),
            )
        assert main(["vocab", "gaps", "--brief"]) == 0
        term = json.loads(capsys.readouterr().out)["dormant"][0]

        assert term["meaning"] == "reduce the risk of a change"
        assert term["notes"] == "misused"
        assert term["times_seen"] == 2
        assert term["times_used_correctly"] == 0
        for dropped in ("example", "due_date", "interval_days", "source", "id"):
            assert dropped not in term

    def test_brief_leaves_the_headline_numbers_alone(self, data_dir, capsys):
        """Section 4 of the feedback opens with `activation_rate` and the weakest
        `by_kind` entry, so trimming the term records must not touch the stats."""
        import json

        from app.cli import main

        with dbmod.cursor() as conn:
            seed(conn, "de-risk", seen=2, correct=0)
            seed(conn, "bottleneck", seen=2, correct=2)
        assert main(["vocab", "gaps", "--brief"]) == 0
        brief = json.loads(capsys.readouterr().out)
        assert main(["vocab", "gaps"]) == 0
        full = json.loads(capsys.readouterr().out)

        assert brief["stats"] == full["stats"]
        assert brief["by_kind"] == full["by_kind"]


class TestLearningNotes:
    def test_seeded_and_never_clobbered(self, data_dir):
        from app.config import settings
        from app.paths import seed_notes

        assert settings.notes_path.is_file()
        settings.notes_path.write_text("# mine\n\n- hand-edited\n", encoding="utf-8")
        seed_notes()
        assert "hand-edited" in settings.notes_path.read_text(encoding="utf-8")

    def test_is_a_separate_file_from_the_profile(self, data_dir):
        """They accumulate at different rates and are read for different reasons -
        folding the notes into the profile would bloat the one file `/generate-topic`
        reads in full every run."""
        from app.config import settings

        assert settings.notes_path != settings.profile_path
        assert settings.notes_path.is_file() and settings.profile_path.is_file()


class TestNotesApi:
    def test_get_seeds_and_returns_a_version(self, client):
        body = client.get("/api/notes").json()
        assert body["markdown"]
        assert body["path"].endswith("learning-notes.md")
        assert body["version"]

    def test_save_round_trips(self, client):
        version = client.get("/api/notes").json()["version"]
        saved = client.put("/api/notes", json={"markdown": "# mine\n", "version": version})
        assert saved.status_code == 200
        assert saved.json()["version"] != version
        assert client.get("/api/notes").json()["markdown"] == "# mine\n"

    def test_a_stale_version_is_refused_not_overwritten(self, client):
        """The conflict this exists for: `/process-session` edits the same file from a
        terminal while an editor is open in the browser. Months of coaching notes are
        gitignored, so a silent clobber is unrecoverable."""
        stale = client.get("/api/notes").json()["version"]
        fresh = client.put("/api/notes", json={"markdown": "# from claude\n", "version": stale})
        assert fresh.status_code == 200

        clash = client.put("/api/notes", json={"markdown": "# from browser\n", "version": stale})
        assert clash.status_code == 409
        assert "process-session" in clash.json()["detail"]
        assert client.get("/api/notes").json()["markdown"] == "# from claude\n"

    def test_omitting_the_version_forces_the_write(self, client):
        """The escape hatch for a client that has no version to offer - deliberate, and
        the only way past the guard."""
        client.put("/api/notes", json={"markdown": "# first\n", "version": None})
        assert client.get("/api/notes").json()["markdown"] == "# first\n"

    def test_a_blank_save_is_refused(self, client):
        version = client.get("/api/notes").json()["version"]
        response = client.put("/api/notes", json={"markdown": "   \n", "version": version})
        assert response.status_code == 400
        assert client.get("/api/notes").json()["markdown"].strip()


@pytest.mark.parametrize(
    ("source", "seen", "correct", "expected"),
    [
        ("recommended", 0, 0, "untried"),
        ("recommended", 1, 0, "dormant"),
        ("recommended", 3, 1, "shaky"),
        ("recommended", 1, 1, "active"),
        ("user_speech", 0, 0, "active"),
    ],
)
def test_bucket_boundaries(source, seen, correct, expected):
    word = {"source": source, "times_seen": seen, "times_used_correctly": correct}
    assert dbmod._gap_bucket(word) == expected
