"""GPU/DLL setup that must happen before CTranslate2 is imported.

On Windows the pip wheels ship their own CUDA DLLs (torch/lib holds cublas +
the full cuDNN 9 set, ctranslate2 holds cudnn64_9). CTranslate2 loads them
lazily via the OS loader, which only searches directories registered with
``os.add_dll_directory`` under Python 3.8+. Registering them up front avoids the
classic "Library cudnn_ops64_9.dll is not found" crash on the first transcribe.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path


@lru_cache
def prepare_cuda_dlls() -> list[str]:
    """Register wheel-bundled CUDA DLL directories. Returns the ones added."""
    if os.name != "nt":
        return []
    added: list[str] = []
    for module in ("torch", "ctranslate2", "nvidia"):
        try:
            base = Path(__import__(module).__file__).parent
        except Exception:
            continue
        candidates = [base / "lib", base]
        candidates += list(base.glob("*/bin"))  # nvidia/<lib>/bin layout
        for folder in candidates:
            if not folder.is_dir() or str(folder) in added:
                continue
            if not any(folder.glob("*.dll")):
                continue
            try:
                os.add_dll_directory(str(folder))
            except OSError:
                continue
            added.append(str(folder))
    return added


def gpu_report() -> dict[str, object]:
    """Diagnostics for `ect doctor` - never raises."""
    report: dict[str, object] = {
        "python": sys.version.split()[0],
        "dll_dirs": prepare_cuda_dlls(),
    }
    try:
        import torch

        report["torch"] = torch.__version__
        report["cuda_available"] = bool(torch.cuda.is_available())
        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info()
            report["gpu"] = torch.cuda.get_device_name(0)
            report["vram_total_gb"] = round(total / 1024**3, 2)
            report["vram_free_gb"] = round(free / 1024**3, 2)
    except Exception as exc:  # pragma: no cover - environment dependent
        report["torch_error"] = repr(exc)
    try:
        import ctranslate2

        report["ctranslate2"] = ctranslate2.__version__
    except Exception as exc:  # pragma: no cover
        report["ctranslate2_error"] = repr(exc)
    return report
