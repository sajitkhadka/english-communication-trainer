"""The `data/` tree (PRD 7.3). One place that knows where files live."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from .config import settings

MODES = ("recommended", "freeform", "interview", "worklog", "brainstorm", "journal")


def slugify(text: str, *, max_len: int = 40) -> str:
    """Ascii kebab-case, capped - the human-readable half of a titled filename."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:max_len].strip("-") or "untitled"


def ensure_tree() -> None:
    """Create every directory the app writes to. Safe to call repeatedly."""
    for mode in MODES:
        (settings.data_dir / "recordings" / mode).mkdir(parents=True, exist_ok=True)
    for sub in ("transcripts", "feedback", "prompts", "queue", "brainstorm"):
        (settings.data_dir / sub).mkdir(parents=True, exist_ok=True)
    for sub in ("daily", "monthly", "prep"):
        (settings.data_dir / "worklog" / sub).mkdir(parents=True, exist_ok=True)
    settings.docs_dir.mkdir(parents=True, exist_ok=True)
    seed_profile()
    seed_notes()


def _seed(live: Path, template: Path, fallback: str) -> Path:
    """Copy a tracked template to its live `data/` counterpart on a fresh checkout.

    Never overwrites: these are the files the user and Claude both edit by hand, and
    they are not in git, so a clobber is unrecoverable.
    """
    if not live.is_file():
        live.parent.mkdir(parents=True, exist_ok=True)
        live.write_text(
            template.read_text(encoding="utf-8") if template.is_file() else fallback,
            encoding="utf-8",
        )
    return live


def seed_profile() -> Path:
    """`data/profile.md` - who the user is, read by `/generate-topic` (PRD 7.1)."""
    return _seed(settings.profile_path, settings.profile_template_path, "# User Profile\n")


def seed_notes() -> Path:
    """`data/learning-notes.md` - the durable English-learning wiki (see config.Settings).

    Separate from the profile because the two accumulate at different rates and are
    read for different reasons: the profile says who is speaking, the notes say what
    has already been taught.
    """
    return _seed(settings.notes_path, settings.notes_template_path, "# Learning Notes\n")


def recording_path(session_id: int, mode: str, suffix: str = ".wav") -> Path:
    if mode not in MODES:
        raise ValueError(f"unknown mode: {mode!r}")
    return settings.data_dir / "recordings" / mode / f"{session_id}{suffix}"


def find_recording(session_id: int, mode: str) -> Path | None:
    """Browsers hand us webm/ogg/mp4 depending on the platform, so the extension varies."""
    folder = settings.data_dir / "recordings" / mode
    if not folder.is_dir():
        return None
    matches = sorted(folder.glob(f"{session_id}.*"))
    return matches[0] if matches else None


def transcript_path(session_id: int) -> Path:
    return settings.data_dir / "transcripts" / f"{session_id}.json"


def transcript_text_path(session_id: int) -> Path:
    """Plain-text sibling of `transcript_path` - no word timings, no measurements.

    What the lean, mode-aware brief reads for `worklog`/`brainstorm`, and what a
    `journal` session's entry *is* (see app/brief.py, services.py)."""
    return settings.data_dir / "transcripts" / f"{session_id}.txt"


def feedback_path(session_id: int, slug: str | None = None) -> Path:
    name = f"{session_id}-{slug}.md" if slug else f"{session_id}.md"
    return settings.data_dir / "feedback" / name


def brainstorm_path(session_id: int, slug: str | None = None) -> Path:
    name = f"{session_id}-{slug}.md" if slug else f"{session_id}.md"
    return settings.data_dir / "brainstorm" / name


def prompt_path(session_id: int) -> Path:
    return settings.data_dir / "prompts" / f"{session_id}.json"


def queue_marker(session_id: int) -> Path:
    return settings.data_dir / "queue" / f"{session_id}.pending"


def worklog_daily_path(entry_date: str, slug: str | None = None) -> Path:
    name = f"{entry_date}-{slug}.md" if slug else f"{entry_date}.md"
    return settings.data_dir / "worklog" / "daily" / name


def worklog_rollup_path(month: str) -> Path:
    return settings.data_dir / "worklog" / "monthly" / f"{month}.md"


def relpath(path: Path | str | None) -> str | None:
    """Store paths relative to the repo root so the DB stays portable."""
    if path is None:
        return None
    from .config import REPO_ROOT

    p = Path(path)
    try:
        return p.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return p.as_posix()


def abspath(rel: str | None) -> Path | None:
    if not rel:
        return None
    from .config import REPO_ROOT

    p = Path(rel)
    return p if p.is_absolute() else (REPO_ROOT / p)
