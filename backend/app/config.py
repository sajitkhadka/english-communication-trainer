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
    # Loopback by default, and deliberately so: `PUT /api/notes` and
    # `DELETE /api/sessions/{id}` are unauthenticated. ADR 0006 moves this to the PC's
    # LAN address so the relay can reach it, and pairs that with a firewall rule scoped
    # to the relay host - the rule is load-bearing, not hygiene. See docs/relay.md.
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # --- relay / agent (ADR 0006) ---
    # Empty `relay_url` disables the agent entirely: `ect agent run` refuses to start
    # rather than looping against nothing, and nothing else in the app changes.
    relay_url: str = ""
    relay_token: str = ""
    # What the agent calls to drive the workflow. Deliberately the HTTP API and not
    # `services`: every WhisperX load has to happen inside the one server process, or
    # `services._gpu_lock` (a threading.Lock, so process-wide only) stops guarding
    # anything and two concurrent large-v3 loads take the worker down natively.
    local_api_url: str = "http://127.0.0.1:8000"
    agent_poll_sec: float = 20.0  # inbox drain + heartbeat
    agent_digest_sec: float = 120.0  # digest rebuild; pushed only when its hash moves
    agent_http_timeout_sec: float = 30.0
    # Transcription is minutes of GPU work behind a synchronous request.
    agent_transcribe_timeout_sec: float = 1800.0
    # How many of the most recent sessions carry full feedback markdown in the digest.
    # Beyond it, metadata only - the snapshot is for reading history, not replaying it.
    digest_feedback_horizon: int = 50

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

    @property
    def notes_path(self) -> Path:
        """The live learning notes: sentence patterns, phrases being activated,
        recurring corrections. Personal state like the profile, so it lives in `data/`
        for the same reasons - but deliberately a separate file, because `profile.md`
        is read in full on every `/generate-topic` run and has to stay short, while
        this one is meant to grow. `docs/learning-notes.example.md` is its seed."""
        return self.data_dir / "learning-notes.md"

    @property
    def notes_template_path(self) -> Path:
        return self.docs_dir / "learning-notes.example.md"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
