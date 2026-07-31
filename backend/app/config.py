"""Central configuration. Every path in the app derives from here."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> backend/app -> backend -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Env-overridable settings. Prefix every var with ``ECT_``."""

    model_config = SettingsConfigDict(env_prefix="ECT_", env_file=".env", extra="ignore")

    # --- storage ---
    data_dir: Path = REPO_ROOT / "data"
    docs_dir: Path = REPO_ROOT / "docs"

    # --- transcription (see PRD 5.3 for the fallback ladder) ---
    whisper_model: str = "large-v3"
    compute_type: str = "int8_float16"
    device: str = "cuda"
    batch_size: int = 8
    language: str = "en"
    align: bool = True
    # Where HuggingFace/whisperx cache model weights. Kept inside the repo (gitignored)
    # so the ~3 GB of weights are easy to find and delete.
    model_cache_dir: Path = REPO_ROOT / "models"

    # --- audio analysis thresholds ---
    min_pause_sec: float = 0.30  # silence below this is normal articulation, not a pause
    long_pause_sec: float = 1.50
    # A VAD speech segment at least this long whose word coverage is below
    # `hesitation_max_word_coverage` is treated as a vocalized filler Whisper dropped.
    min_hesitation_sec: float = 0.25
    hesitation_max_word_coverage: float = 0.40

    # --- behaviour ---
    transcribe_on_upload: bool = True  # run the GPU pipeline as soon as audio lands

    # --- server ---
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def profile_path(self) -> Path:
        """The live profile. It lives in `data/`, not `docs/`, because it is personal
        state rather than documentation: `/process-session` fills it with employer,
        projects and weaknesses, and `data/` is the tree git ignores and backups cover.
        `docs/profile.example.md` is the tracked seed it starts from."""
        return self.data_dir / "profile.md"

    @property
    def profile_template_path(self) -> Path:
        return self.docs_dir / "profile.example.md"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
