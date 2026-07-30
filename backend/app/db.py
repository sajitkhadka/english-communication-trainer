"""SQLite access layer. Plain sqlite3 - the schema is small and stable (PRD 7.1)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .config import settings
from .paths import ensure_tree

SCHEMA_FILE = Path(__file__).with_name("schema.sql")

SESSION_STATUSES = ("awaiting_recording", "recorded", "pending", "processed")
SCORE_DIMENSIONS = (
    "vocab_range",
    "filler_density",
    "fluency",
    "grammar",
    "structure",
    "coherence",
    "target_usage",
)
# Weights for `overall` (PRD 9). Computed in code, never by Claude, so the number
# is comparable across every session. `target_usage` is dropped and the remaining
# weights renormalised for free-form sessions, where it is N/A.
SCORE_WEIGHTS = {
    "vocab_range": 0.18,
    "filler_density": 0.15,
    "fluency": 0.15,
    "grammar": 0.15,
    "structure": 0.14,
    "coherence": 0.15,
    "target_usage": 0.08,
}


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def today() -> str:
    # Local date, deliberately. Review scheduling should follow the user's day, not
    # UTC's - practising at 11pm must not land the next review on "tomorrow" already.
    return date.today().isoformat()  # noqa: DTZ011


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def cursor(db_path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """Transactional connection: commits on clean exit, rolls back on error."""
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path | str | None = None) -> Path:
    ensure_tree()
    path = Path(db_path) if db_path else settings.db_path
    with cursor(path) as conn:
        conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
    return path


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


# --------------------------------------------------------------------------- #
# sessions
# --------------------------------------------------------------------------- #


def create_session(
    conn: sqlite3.Connection,
    *,
    mode: str,
    topic: str | None = None,
    category: str | None = None,
    target_words: list[str] | None = None,
    status: str = "awaiting_recording",
    notes: str | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO sessions (mode, category, topic, target_words, status, created_at, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            mode,
            category,
            topic,
            json.dumps(target_words or []),
            status,
            utcnow(),
            notes,
        ),
    )
    return int(cur.lastrowid)


def get_session(conn: sqlite3.Connection, session_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return hydrate_session(row) if row else None


def hydrate_session(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["target_words"] = _loads(data.get("target_words"), [])
    return data


def list_sessions(
    conn: sqlite3.Connection,
    *,
    mode: str | None = None,
    status: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM sessions WHERE 1=1"
    args: list[Any] = []
    if mode:
        sql += " AND mode = ?"
        args.append(mode)
    if status:
        sql += " AND status = ?"
        args.append(status)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    return [hydrate_session(r) for r in conn.execute(sql, args)]


def update_session(conn: sqlite3.Connection, session_id: int, **fields: Any) -> None:
    allowed = {
        "mode",
        "category",
        "topic",
        "target_words",
        "status",
        "audio_path",
        "transcript_path",
        "feedback_path",
        "processed_at",
        "transcribe_status",
        "transcribe_error",
        "duration_sec",
        "notes",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"cannot update unknown session columns: {sorted(unknown)}")
    if not fields:
        return
    if isinstance(fields.get("target_words"), list):
        fields["target_words"] = json.dumps(fields["target_words"])
    # Column names come from the `allowed` set above, never from the caller's data;
    # every value is still bound as a parameter.
    assignments = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE sessions SET {assignments} WHERE id = ?",  # noqa: S608
        [*fields.values(), session_id],
    )


# --------------------------------------------------------------------------- #
# scores
# --------------------------------------------------------------------------- #


def compute_overall(scores: dict[str, float | None]) -> float:
    """Weighted mean over the dimensions actually present (PRD 9)."""
    total = 0.0
    weight = 0.0
    for dim, w in SCORE_WEIGHTS.items():
        value = scores.get(dim)
        if value is None:
            continue
        total += float(value) * w
        weight += w
    if weight == 0:
        return 0.0
    return round(total / weight, 2)


def insert_score(
    conn: sqlite3.Connection, session_id: int, scores: dict[str, float | None]
) -> float:
    overall = compute_overall(scores)
    conn.execute(
        """INSERT INTO scores
             (session_id, vocab_range, filler_density, fluency, grammar,
              structure, coherence, target_usage, overall, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session_id,
            *(scores.get(dim) for dim in SCORE_DIMENSIONS),
            overall,
            utcnow(),
        ),
    )
    return overall


def get_score(conn: sqlite3.Connection, session_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM scores WHERE session_id = ? ORDER BY id DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    return row_to_dict(row)


def score_history(conn: sqlite3.Connection, limit: int = 200) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT sc.*, s.mode, s.topic, s.category
             FROM scores sc JOIN sessions s ON s.id = sc.session_id
            ORDER BY sc.id ASC LIMIT ?""",
        (limit,),
    )
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# words
# --------------------------------------------------------------------------- #


def get_word_by_term(conn: sqlite3.Connection, term: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM words WHERE lower(term) = lower(?)", (term.strip(),)
    ).fetchone()
    return row_to_dict(row)


def add_word(
    conn: sqlite3.Connection,
    *,
    term: str,
    kind: str | None = None,
    meaning: str | None = None,
    example: str | None = None,
    source: str | None = None,
    notes: str | None = None,
) -> int:
    """Insert a word, or enrich the existing row without clobbering good data."""
    term = term.strip()
    existing = get_word_by_term(conn, term)
    if existing:
        conn.execute(
            """UPDATE words
                  SET kind    = COALESCE(?, kind),
                      meaning = COALESCE(?, meaning),
                      example = COALESCE(?, example),
                      notes   = COALESCE(?, notes)
                WHERE id = ?""",
            (kind, meaning, example, notes, existing["id"]),
        )
        return int(existing["id"])
    cur = conn.execute(
        """INSERT INTO words (term, kind, meaning, example, first_seen, due_date, source, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (term, kind, meaning, example, today(), today(), source, notes),
    )
    return int(cur.lastrowid)


def due_words(
    conn: sqlite3.Connection, limit: int = 15, on_date: str | None = None
) -> list[dict[str, Any]]:
    """Words due for review: least mastered and longest overdue first."""
    rows = conn.execute(
        """SELECT * FROM words
            WHERE due_date IS NULL OR due_date <= ?
            ORDER BY mastery ASC, due_date ASC, times_seen ASC
            LIMIT ?""",
        (on_date or today(), limit),
    )
    return [dict(r) for r in rows]


def list_words(
    conn: sqlite3.Connection, *, sort: str = "recency", limit: int = 1000
) -> list[dict[str, Any]]:
    order = {
        "recency": "COALESCE(last_practiced, first_seen) DESC, id DESC",
        "frequency": "times_seen DESC, id DESC",
        "mastery": "mastery ASC, due_date ASC",
        "alpha": "lower(term) ASC",
        "due": "due_date ASC",
    }.get(sort, "id DESC")
    # `order` is one of the literals above - an unrecognised `sort` falls back rather
    # than reaching the query.
    rows = conn.execute(f"SELECT * FROM words ORDER BY {order} LIMIT ?", (limit,))  # noqa: S608
    return [dict(r) for r in rows]


def word_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        """SELECT COUNT(*)                                        AS total,
                  SUM(CASE WHEN due_date <= ? THEN 1 ELSE 0 END)  AS due,
                  SUM(CASE WHEN mastery >= 0.8 THEN 1 ELSE 0 END) AS mastered,
                  SUM(CASE WHEN times_seen = 0 THEN 1 ELSE 0 END) AS never_practiced,
                  AVG(mastery)                                    AS avg_mastery
             FROM words""",
        (today(),),
    ).fetchone()
    # sqlite3.Row yields values when iterated, so .keys() is required here.
    stats = {k: (row[k] or 0) for k in row.keys()}  # noqa: SIM118
    stats["avg_mastery"] = round(float(stats["avg_mastery"] or 0), 3)
    return stats


def record_word_usage(
    conn: sqlite3.Connection,
    *,
    word_id: int,
    session_id: int,
    used: bool,
    used_correctly: bool,
) -> None:
    conn.execute(
        """INSERT INTO word_session_usage (word_id, session_id, used, used_correctly)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(word_id, session_id) DO UPDATE
             SET used = excluded.used, used_correctly = excluded.used_correctly""",
        (word_id, session_id, int(used), int(used_correctly)),
    )


def words_for_session(conn: sqlite3.Connection, session_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT w.term, w.kind, w.meaning, u.used, u.used_correctly
             FROM word_session_usage u JOIN words w ON w.id = u.word_id
            WHERE u.session_id = ?""",
        (session_id,),
    )
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# suggestions
# --------------------------------------------------------------------------- #


def add_suggestion(
    conn: sqlite3.Connection,
    *,
    mode: str,
    topic: str,
    category: str | None = None,
    target_words: list[str] | None = None,
    rationale: str | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO suggestions (mode, category, topic, target_words, rationale, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (mode, category, topic, json.dumps(target_words or []), rationale, utcnow()),
    )
    return int(cur.lastrowid)


def list_suggestions(
    conn: sqlite3.Connection, *, include_used: bool = False
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM suggestions"
    if not include_used:
        sql += " WHERE consumed_by IS NULL"
    sql += " ORDER BY id DESC"
    out = []
    for row in conn.execute(sql):
        item = dict(row)
        item["target_words"] = _loads(item.get("target_words"), [])
        out.append(item)
    return out


def add_suggestion_request(
    conn: sqlite3.Connection, *, mode: str, category: str | None
) -> int:
    cur = conn.execute(
        "INSERT INTO suggestion_requests (mode, category, created_at) VALUES (?, ?, ?)",
        (mode, category, utcnow()),
    )
    return int(cur.lastrowid)


def open_suggestion_requests(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM suggestion_requests WHERE status = 'open' ORDER BY id ASC"
    )
    return [dict(r) for r in rows]
