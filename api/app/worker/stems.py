"""Demucs stem separation (vocals / no_vocals) via the real CLI."""

from __future__ import annotations

import sys
from pathlib import Path

from ..config import Settings
from . import errors
from .errors import PipelineError


def build_demucs_cmd(settings: Settings, audio_in: str, out_dir: str) -> list[str]:
    """Two-stem separation with a configurable model and device.

    Invoked as ``python -m demucs.separate`` so it works wherever the
    package is importable, regardless of console-script installation.
    """
    return [
        sys.executable, "-m", "demucs.separate",
        "-n", settings.demucs_model,
        "--two-stems", "vocals",
        "-d", settings.demucs_device,
        "-j", str(settings.demucs_jobs),
        "-o", out_dir,
        audio_in,
    ]


def locate_stems(
    settings: Settings, audio_in: str, out_dir: str
) -> tuple[Path, Path]:
    """Demucs writes ``<out>/<model>/<track>/{vocals,no_vocals}.wav``."""
    track = Path(audio_in).stem
    stem_dir = Path(out_dir) / settings.demucs_model / track
    vocals = stem_dir / "vocals.wav"
    no_vocals = stem_dir / "no_vocals.wav"
    if not vocals.is_file() or not no_vocals.is_file():
        raise PipelineError(
            errors.DEMUCS_FAILED,
            f"expected stems missing under {stem_dir}",
        )
    return vocals, no_vocals
