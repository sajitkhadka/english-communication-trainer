"""Audio loading. Goes through ffmpeg rather than torchaudio.

The browser's MediaRecorder hands us webm/opus (Chrome) or mp4 (Safari), and the
torchaudio build in this environment has no working torchcodec backend on Windows.
ffmpeg decodes everything and is already a hard requirement of the stack.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16_000


class AudioError(RuntimeError):
    pass


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise AudioError("ffmpeg not found on PATH - see docs/setup.md")
    return exe


def _ffprobe() -> str | None:
    return shutil.which("ffprobe")


def load_audio(path: Path | str, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Decode any container to mono float32 in [-1, 1] at `sr` Hz."""
    path = Path(path)
    if not path.is_file():
        raise AudioError(f"audio file not found: {path}")
    cmd = [
        _ffmpeg(), "-nostdin", "-threads", "0",
        "-i", str(path),
        "-f", "s16le", "-ac", "1", "-acodec", "pcm_s16le", "-ar", str(sr),
        "-",
    ]  # fmt: skip
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-4:]
        raise AudioError(f"ffmpeg failed to decode {path.name}: {' | '.join(tail)}")
    samples = np.frombuffer(proc.stdout, dtype=np.int16)
    if samples.size == 0:
        raise AudioError(f"{path.name} decoded to zero samples (empty or corrupt recording)")
    return samples.astype(np.float32) / 32768.0


def duration_of(path: Path | str) -> float | None:
    """Container duration in seconds, or None when ffprobe can't tell."""
    probe = _ffprobe()
    if not probe:
        return None
    proc = subprocess.run(
        [probe, "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True,
        check=False,
    )  # fmt: skip
    if proc.returncode != 0:
        return None
    try:
        value = json.loads(proc.stdout)["format"]["duration"]
        return round(float(value), 3)
    except (KeyError, ValueError, json.JSONDecodeError):
        return None


def to_wav(src: Path | str, dest: Path | str, sr: int = SAMPLE_RATE) -> Path:
    """Transcode to 16 kHz mono PCM wav (used to normalise browser uploads)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [_ffmpeg(), "-nostdin", "-y", "-i", str(src),
         "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le", str(dest)],
        capture_output=True,
        check=False,
    )  # fmt: skip
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-4:]
        raise AudioError(f"ffmpeg failed to transcode {Path(src).name}: {' | '.join(tail)}")
    return dest


def transcode_opus(
    src: Path | str,
    dest: Path | str,
    *,
    bitrate_kbps: int = 24,
) -> Path:
    """Re-encode speech to low-bitrate mono Opus for archival.

    MediaRecorder hands us Opus at a flat ~129 kbps regardless of content, which is
    roughly five times what mono speech needs. This is a lossy-to-lossy re-encode, so
    it is only ever applied to the *archive* copy - the transcript and every metric in
    `pipeline/metrics.py` are already computed from the original, and nothing
    downstream re-derives them from here.

    `-application voip` tunes libopus for a single speaking voice rather than music,
    which is what makes 24 kbps listenable instead of merely intelligible.
    """
    src, dest = Path(src), Path(dest)
    if not src.is_file():
        raise AudioError(f"audio file not found: {src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _ffmpeg(), "-v", "error", "-y", "-i", str(src),
        "-c:a", "libopus", "-b:a", f"{bitrate_kbps}k", "-ac", "1",
        "-application", "voip", str(dest),
    ]  # fmt: skip
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not dest.is_file():
        raise AudioError(f"ffmpeg could not transcode {src.name}: {proc.stderr.strip()[:300]}")
    # A transcode that produced a file we cannot read back is worse than no archive at
    # all, because it invites deleting the original against a corrupt copy.
    if duration_of(dest) is None:
        dest.unlink(missing_ok=True)
        raise AudioError(f"transcoded {src.name} but the result would not decode")
    return dest
