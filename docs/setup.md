# Setup

Verified on the target machine: Windows 11, RTX 3060 Laptop (6 GB VRAM), driver 610.62 /
CUDA 13.3, Python 3.12, ffmpeg 7.0.2.

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.12 | WhisperX's dependency set does not resolve on 3.13; `requires-python` pins `>=3.12,<3.13`. `uv` will fetch 3.12 if it is not installed. |
| [uv](https://docs.astral.sh/uv/) | Manages the venv and the lockfile. |
| ffmpeg on `PATH` | All audio decoding goes through it — browsers upload webm/opus or mp4, not wav. `ffprobe` (same package) is used for durations. |
| NVIDIA driver | Any driver new enough for CUDA 12.6 runtimes. The cu126 wheels bundle their own CUDA + cuDNN 9, so **no system CUDA toolkit install is needed**. |
| Node 20+ | Frontend only. |

## Backend

```bash
cd backend
uv sync --extra dev      # creates .venv and installs from uv.lock
uv run ect db init       # schema + data/ tree
uv run ect doctor        # verify GPU, ffmpeg, db. Exit code 1 if unusable.
```

A healthy `doctor`:

```json
{ "torch": "2.8.0+cu126", "cuda_available": true,
  "gpu": "NVIDIA GeForce RTX 3060 Laptop GPU",
  "vram_total_gb": 6.0, "vram_free_gb": 5.01,
  "ctranslate2": "4.8.1", "ffmpeg": "C:\\…\\ffmpeg.EXE" }
```

Run the API:

```bash
uv run uvicorn app.main:app --reload --port 8000
```

`http://127.0.0.1:8000/docs` for the OpenAPI browser.

## Frontend

```bash
cd frontend
npm install
npm run dev              # http://localhost:5173, proxies /api to :8000
```

Recording needs a secure context for `getUserMedia`. `localhost` counts as secure, so
the dev server works with no certificate setup. Reaching it over LAN IP would need
HTTPS.

## Models

Downloaded on first transcribe, cached in `models/` (gitignored):

| Model | Size | Purpose |
|---|---|---|
| `large-v3` (CTranslate2) | ~3 GB | transcription |
| wav2vec2 English alignment | ~360 MB | word-level timestamps |
| pyannote segmentation | ~17 MB | WhisperX's internal VAD chunking |
| Silero VAD | ~2 MB | ships inside the `silero-vad` wheel |

First run therefore takes a few minutes on the download. Afterwards a 25-second clip
transcribes and aligns in about 8-10 seconds; a 3-5 minute clip lands well under a
minute (PRD 5.3).

## VRAM and the compute-type ladder

`large-v3 @ int8_float16` is ~3 GB, which leaves room for the alignment model on a 6 GB
card. Configured in `backend/app/config.py` and overridable by env var:

```bash
ECT_COMPUTE_TYPE=int8      # ~2 GB, first fallback
ECT_WHISPER_MODEL=medium   # second fallback
ECT_DEVICE=cpu             # slow, but proves the rest of the pipeline
```

`pipeline/transcribe.py` walks the ladder itself: if the configured precision fails to
load (OOM, unsupported), it degrades to the next one, logs a warning, and records what
it actually used in `transcript.json`'s `meta.compute_type`. You do not need to
intervene on a bad day, but the transcript tells you it happened.

If a *second* process is holding VRAM (a browser doing WebGL, a previous uvicorn that
did not exit), `large-v3` may fail to load where it succeeded before. `ect doctor`
reports `vram_free_gb` — check that first.

## Configuration

Every setting in `backend/app/config.py` is overridable with an `ECT_`-prefixed env var
or a `backend/.env` file. The ones worth knowing:

| Var | Default | Effect |
|---|---|---|
| `ECT_WHISPER_MODEL` | `large-v3` | ASR model |
| `ECT_COMPUTE_TYPE` | `int8_float16` | precision (see ladder above) |
| `ECT_DEVICE` | `cuda` | `cpu` to run without a GPU |
| `ECT_MIN_PAUSE_SEC` | `0.30` | silence below this is articulation, not a pause |
| `ECT_LONG_PAUSE_SEC` | `1.50` | boundary of the "long" pause bucket |
| `ECT_MIN_HESITATION_SEC` | `0.25` | shortest voiced span that can count as a hesitation |
| `ECT_HESITATION_MAX_WORD_COVERAGE` | `0.40` | a voiced span less covered than this by aligned words is an untranscribed filler |
| `ECT_TRANSCRIBE_ON_UPLOAD` | `true` | run the GPU pipeline when Process is pressed, so the skill never waits on it |
| `ECT_ALIGN` | `true` | `false` skips forced alignment (faster, coarser timings) |

Tuning note: the two hesitation thresholds are the knobs that matter. If genuine pauses
are being reported as vocalized fillers, raise `ECT_MIN_HESITATION_SEC`. If obvious
"um"s are being missed, raise `ECT_HESITATION_MAX_WORD_COVERAGE` toward `0.6`.

## Tests

```bash
cd backend && uv run pytest -q
```

The suite covers the metrics layer, SM-2, the score weighting, and the API against a
temporary database. No GPU or model download required — the pipeline is injected with
synthetic word/VAD data, which is exactly why `metrics.py` takes plain values rather
than reaching for the audio itself.

## Troubleshooting

**`Library cudnn_ops64_9.dll is not found`** — CTranslate2 could not find the wheel's
cuDNN. `pipeline/gpu.py` registers `torch/lib` and the `ctranslate2` package directory
via `os.add_dll_directory` before the import; check `ect doctor`'s `dll_dirs` lists them.

**`ffmpeg not found on PATH`** — install it (`choco install ffmpeg`) and restart the
shell so `PATH` refreshes.

**Alignment silently skipped** — `meta.aligned: false` in `transcript.json` means
wav2vec2 failed to load and timings fell back to segment level. Pauses and hesitations
still work (they come from Silero), but per-word subtitle sync is coarse. The warning is
in the server log.

**A `torchcodec` warning on import** — harmless. All decoding goes through the ffmpeg
subprocess in `pipeline/audio.py`, not torchaudio's codec backend.

**Browser records nothing** — the tab must have microphone permission and a secure
context. Check `chrome://settings/content/microphone` for `localhost:5173`.
