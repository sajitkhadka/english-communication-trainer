"""The `data/` tree (PRD 7.3). One place that knows where files live."""

from __future__ import annotations

from pathlib import Path

from .config import settings

MODES = ("recommended", "freeform", "interview")


def ensure_tree() -> None:
    """Create every directory the app writes to. Safe to call repeatedly."""
    for mode in MODES:
        (settings.data_dir / "recordings" / mode).mkdir(parents=True, exist_ok=True)
    for sub in ("transcripts", "feedback", "prompts", "queue"):
        (settings.data_dir / sub).mkdir(parents=True, exist_ok=True)
    settings.docs_dir.mkdir(parents=True, exist_ok=True)
    seed_profile()


def seed_profile() -> Path:
    """Copy the tracked template to `data/profile.md` on a fresh checkout.

    Never overwrites: the live profile is the one file the user and Claude both edit by
    hand, and it is not in git, so a clobber is unrecoverable.
    """
    profile = settings.profile_path
    if not profile.is_file():
        template = settings.profile_template_path
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text(
            template.read_text(encoding="utf-8") if template.is_file() else "# User Profile\n",
            encoding="utf-8",
        )
    return profile


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


def feedback_path(session_id: int) -> Path:
    return settings.data_dir / "feedback" / f"{session_id}.md"


def prompt_path(session_id: int) -> Path:
    return settings.data_dir / "prompts" / f"{session_id}.json"


def queue_marker(session_id: int) -> Path:
    return settings.data_dir / "queue" / f"{session_id}.pending"


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
