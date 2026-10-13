"""Demucs stem separation (vocals / no_vocals) via the real CLI."""

from __future__ import annotations

import sys
import wave
from pathlib import Path

from ..config import Settings
from . import errors, media
from .errors import PipelineError


def wav_duration_seconds(path: str) -> float:
    with wave.open(path, "rb") as handle:
        rate = handle.getframerate()
        if rate <= 0:
            return 0.0
        return handle.getnframes() / float(rate)


def chunk_ranges(duration_seconds: float, chunk_seconds: float) -> list[tuple[float, float]]:
    """Cover ``duration_seconds`` with ``(start, length)`` slices in seconds."""
    if duration_seconds <= 0:
        return [(0.0, 0.0)]
    size = max(float(chunk_seconds), 1.0)
    ranges: list[tuple[float, float]] = []
    start = 0.0
    while start < duration_seconds - 0.02:
        length = min(size, duration_seconds - start)
        ranges.append((start, length))
        start += length
    return ranges or [(0.0, duration_seconds)]


def build_demucs_cmd(settings: Settings, audio_in: str, out_dir: str) -> list[str]:
    """Two-stem separation with a configurable model and device.

    Invoked via ``app.demucs_separate_shim`` so WAV I/O uses soundfile
    instead of TorchCodec (which needs FFmpeg DLLs on Windows).
    """
    return [
        sys.executable, "-m", "app.demucs_separate_shim",
        "-n", settings.demucs_model,
        "--two-stems", "vocals",
        "-d", settings.demucs_device,
        "-j", str(settings.demucs_jobs),
        "--segment", str(settings.demucs_segment_seconds),
        "-o", out_dir,
        audio_in,
    ]


def build_wav_slice_cmd(
    settings: Settings, source: str, wav_out: str, start: float, length: float
) -> list[str]:
    return [
        media.resolve_media_binary(settings.ffmpeg_path, "ffmpeg"),
        "-y",
        "-nostdin",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{length:.3f}",
        "-i",
        source,
        "-acodec",
        "pcm_s16le",
        "-ar",
        "44100",
        "-ac",
        "2",
        wav_out,
    ]


def write_concat_list(paths: list[Path], list_path: Path) -> None:
    lines: list[str] = []
    for path in paths:
        posix = path.resolve().as_posix().replace("'", r"'\''")
        lines.append(f"file '{posix}'")
    list_path.parent.mkdir(parents=True, exist_ok=True)
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_wav_concat_cmd(settings: Settings, list_path: str, wav_out: str) -> list[str]:
    return [
        media.resolve_media_binary(settings.ffmpeg_path, "ffmpeg"),
        "-y",
        "-nostdin",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        list_path,
        "-c",
        "copy",
        wav_out,
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
