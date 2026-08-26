"""ffprobe / ffmpeg command construction and execution.

Command builders are pure functions (unit-tested without binaries); the
async ``run_command`` wrapper streams stderr, keeps the job heartbeat fresh
during long encodes, and kills the subprocess when the job is cancelled.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import wave
from array import array
from dataclasses import dataclass
from pathlib import PurePath
from typing import Awaitable, Callable

from ..config import Settings
from . import errors
from .errors import JobCancelled, PipelineError
from .timing import atempo_chain, tempo_filters

logger = logging.getLogger("dubby.worker.media")

# Callback invoked periodically while a subprocess runs; raising
# JobCancelled from it kills the process.
HeartbeatFn = Callable[[], Awaitable[None]]


def resolve_media_binary(configured: str, fallback: str) -> str:
    """Pick a runnable ffmpeg/ffprobe, ignoring Windows paths leaked into Linux .env."""
    value = (configured or "").strip() or fallback
    if not sys.platform.startswith("win"):
        # PosixPath treats backslashes as part of the name — reject host paths.
        if "\\" in value or value.lower().endswith(".exe") or (
            len(value) >= 2 and value[1] == ":"
        ):
            logger.warning(
                "ignoring non-Linux media binary %r; using %s", value, fallback
            )
            value = fallback
        if os.path.isabs(value) and not os.path.isfile(value):
            value = fallback
    return value


def _ffmpeg(settings: Settings) -> str:
    return resolve_media_binary(settings.ffmpeg_path, "ffmpeg")


def _ffprobe(settings: Settings) -> str:
    return resolve_media_binary(settings.ffprobe_path, "ffprobe")


_STDERR_TAIL_CHARS = 2000
_ffmpeg_rubberband_cache: dict[str, bool] = {}


def ffmpeg_has_rubberband(ffmpeg_path: str = "ffmpeg") -> bool:
    """True when the ffmpeg binary exposes the pitch-preserving rubberband filter."""
    cached = _ffmpeg_rubberband_cache.get(ffmpeg_path)
    if cached is not None:
        return cached
    result = subprocess.run(
        [ffmpeg_path, "-hide_banner", "-filters"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    blob = f"{result.stdout}\n{result.stderr}"
    available = bool(re.search(r"\brubberband\b", blob))
    _ffmpeg_rubberband_cache[ffmpeg_path] = available
    return available


@dataclass(frozen=True)
class MediaInfo:
    duration_seconds: float
    size_bytes: int
    format_names: frozenset[str]
    has_audio: bool
    has_video: bool


# --- command builders (pure) --------------------------------------------------


def build_probe_cmd(settings: Settings, source: str) -> list[str]:
    return [
        _ffprobe(settings),
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        source,
    ]


def build_audio_extract_cmd(
    settings: Settings, source: str, wav_out: str
) -> list[str]:
    """Full-quality stereo WAV for Demucs / mixing."""
    return [
        _ffmpeg(settings), "-y", "-nostdin",
        "-i", source,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "44100",
        "-ac", "2",
        wav_out,
    ]


def build_asr_audio_cmd(settings: Settings, source: str, mp3_out: str) -> list[str]:
    """Compact mono MP3 that stays under the Whisper API's 25 MB cap.

    Matches local_step12 speech band-limiting (highpass/lowpass) before 16 kHz
    downsample so Whisper sees cleaner dialogue and fewer music hallucinations.
    """
    return [
        _ffmpeg(settings), "-y", "-nostdin",
        "-i", source,
        "-vn",
        "-af", "highpass=f=60,lowpass=f=7800",
        "-acodec", "libmp3lame",
        "-ar", "16000",
        "-ac", "1",
        "-b:a", "64k",
        mp3_out,
    ]


def build_clip_fit_cmd(
    settings: Settings,
    clip_in: str,
    wav_out: str,
    tempo_factor: float,
    *,
    backend: str = "atempo",
    max_seconds: float | None = None,
    gain_db: float = 0.0,
) -> list[str]:
    """Decode/fix tempo and hard-cap duration to prevent adjacent overlap."""
    cmd = [_ffmpeg(settings), "-y", "-nostdin", "-i", clip_in]
    filters: list[str] = []
    if tempo_factor != 1.0:
        filters.extend(
            tempo_filters(
                tempo_factor,
                rubberband_available=(backend == "rubberband"),
            )
        )
    if abs(gain_db) >= 0.01:
        filters.append(f"volume={gain_db:.2f}dB")
    if filters:
        cmd += ["-filter:a", ",".join(filters)]
    if max_seconds is not None:
        cmd += ["-t", f"{max(0.001, max_seconds):.3f}"]
    cmd += ["-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2", wav_out]
    return cmd


def measure_audio_loudness_db(
    path: str,
    start_ms: int,
    end_ms: int,
    *,
    ffmpeg_path: str = "ffmpeg",
) -> float:
    """Measure mean loudness (dBFS) for any audio file via ffmpeg volumedetect."""
    result = subprocess.run(
        [
            ffmpeg_path,
            "-nostdin",
            "-ss",
            f"{start_ms / 1000:.3f}",
            "-i",
            path,
            "-t",
            f"{max(0.001, (end_ms - start_ms) / 1000):.3f}",
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    match = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", result.stderr)
    return float(match.group(1)) if match else -60.0


def measure_pcm16_wav_db(
    path: str,
    start_ms: int,
    end_ms: int,
) -> float:
    """Measure segment RMS in dBFS from a PCM16 vocals stem."""
    with wave.open(path, "rb") as source:
        if source.getsampwidth() != 2:
            raise ValueError("loudness measurement requires PCM16 WAV")
        rate = source.getframerate()
        start_frame = max(0, round(start_ms * rate / 1000))
        frame_count = max(1, round((end_ms - start_ms) * rate / 1000))
        source.setpos(min(start_frame, source.getnframes()))
        samples = array("h", source.readframes(frame_count))
    if not samples:
        return -60.0
    mean_square = sum(sample * sample for sample in samples) / len(samples)
    if mean_square <= 0:
        return -60.0
    return max(-60.0, 20 * math.log10(math.sqrt(mean_square) / 32768))


def build_voice_sample_cmd(
    settings: Settings,
    vocals_in: str,
    mp3_out: str,
    sample_seconds: float,
    ranges_ms: list[tuple[int, int]] | None = None,
) -> list[str]:
    """Trimmed vocals stem used as the ElevenLabs IVC reference sample."""
    # Instant Voice Clone rejects samples shorter than 1 second.
    min_seconds = 1.2
    cmd = [
        _ffmpeg(settings), "-y", "-nostdin",
        "-i", vocals_in,
    ]
    if ranges_ms:
        pieces = []
        labels = []
        consumed = 0.0
        for i, (start, end) in enumerate(ranges_ms):
            duration = min((end - start) / 1000, sample_seconds - consumed)
            if duration <= 0.05:
                continue
            pieces.append(
                f"[0:a]atrim=start={start / 1000:.3f}:duration={duration:.3f},"
                f"asetpts=PTS-STARTPTS[s{i}]"
            )
            labels.append(f"[s{i}]")
            consumed += duration
            if consumed >= sample_seconds:
                break
        if consumed < min_seconds and ranges_ms:
            start = max(0, ranges_ms[0][0])
            pieces = [
                f"[0:a]atrim=start={start / 1000:.3f}:duration={min_seconds:.3f},"
                f"asetpts=PTS-STARTPTS[s0]"
            ]
            labels = ["[s0]"]
            consumed = min_seconds
        if labels:
            pieces.append("".join(labels) + f"concat=n={len(labels)}:v=0:a=1[sample]")
            cmd += ["-filter_complex", ";".join(pieces), "-map", "[sample]"]
            sample_seconds = max(sample_seconds, consumed)
    cmd += [
        "-t", f"{max(sample_seconds, min_seconds):g}",
        "-vn",
        "-acodec", "libmp3lame",
        "-ar", "44100",
        "-ac", "1",
        "-b:a", "128k",
        mp3_out,
    ]
    return cmd


def merge_speech_ranges(
    ranges_ms: list[tuple[int, int]],
    *,
    max_gap_ms: int = 0,
) -> list[tuple[int, int]]:
    """Normalize ASR ranges without filling non-language gaps."""
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges_ms):
        start = max(0, start)
        if end <= start:
            continue
        if merged and start <= merged[-1][1] + max_gap_ms:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def speech_mask_expression(
    ranges_ms: list[tuple[int, int]],
    fade_seconds: float = 0.08,
    leading_padding_seconds: float = 0.28,
    trailing_padding_seconds: float = 0.24,
) -> str:
    """FFmpeg volume mask active only during language recognized by ASR.

    Padding is intentionally generous so short leftover syllables at phrase
    edges are scrubbed in the voice-removed bed.
    """
    masks: list[str] = []
    for start_ms, end_ms in merge_speech_ranges(ranges_ms, max_gap_ms=400):
        start = max(0.0, start_ms / 1000 - leading_padding_seconds)
        end = end_ms / 1000 + trailing_padding_seconds
        fade_in_start = max(0.0, start - fade_seconds)
        fade_in = start - fade_in_start
        fade_out_end = end + fade_seconds
        if fade_in <= 0.001:
            fade_in_expression = f"if(lt(t,{end:.6f}),1,"
        else:
            fade_in_expression = (
                f"if(lt(t,{fade_in_start:.6f}),0,"
                f"if(lt(t,{start:.6f}),"
                f"(t-{fade_in_start:.6f})/{fade_in:.6f},"
                f"if(lt(t,{end:.6f}),1,"
            )
        if fade_seconds <= 0.001:
            masks.append(f"between(t,{start:.6f},{end:.6f})")
            continue
        masks.append(
            fade_in_expression
            + f"if(lt(t,{fade_out_end:.6f}),"
            f"({fade_out_end:.6f}-t)/{fade_seconds:.6f},0)"
            + (")))" if fade_in > 0.001 else ")")
        )
    if not masks:
        return "0"
    expression = masks[0]
    for mask in masks[1:]:
        expression = f"max({expression},{mask})"
    return expression


def build_selective_voice_removal_cmd(
    settings: Settings,
    original_wav: str,
    no_vocals_wav: str,
    ranges_ms: list[tuple[int, int]],
    wav_out: str,
    *,
    no_vocals_in_mask: float = 0.4,
) -> list[str]:
    """Blend to no_vocals only inside ASR-recognized speech ranges.

    ``no_vocals_in_mask`` attenuates Demucs bleed under dialogue windows while
    keeping some non-speech atmosphere (1.0 = full no_vocals stem).
    """
    mask = speech_mask_expression(ranges_ms)
    bleed = max(0.0, min(1.0, float(no_vocals_in_mask)))
    filters = (
        f"[0:a]aresample=44100,volume=eval=frame:volume='1-({mask})'[original];"
        f"[1:a]aresample=44100,volume=eval=frame:volume='({mask})*{bleed:.4f}'[removed];"
        "[original][removed]amix=inputs=2:duration=first:normalize=0[bed]"
    )
    return [
        _ffmpeg(settings), "-y", "-nostdin",
        "-i", original_wav,
        "-i", no_vocals_wav,
        "-filter_complex", filters,
        "-map", "[bed]",
        "-acodec", "pcm_s16le",
        "-ar", "44100",
        "-ac", "2",
        wav_out,
    ]


def build_mix_cmd(
    settings: Settings,
    background_wav: str,
    placed_clips: list[tuple[str, int]],
    wav_out: str,
) -> list[str]:
    """Sum the no_vocals stem with each dubbed clip delayed to its start_ms.

    ``placed_clips``: [(fitted_clip_wav, start_ms), ...]. amix with
    normalize=0 sums instead of attenuating by input count; a limiter keeps
    the sum from clipping. duration=first pins the mix to the background
    (i.e. video) length.
    """
    cmd = [_ffmpeg(settings), "-y", "-nostdin", "-i", background_wav]
    for clip, _ in placed_clips:
        cmd += ["-i", clip]

    filters: list[str] = []
    labels = ["[0:a]"]
    for i, (_, start_ms) in enumerate(placed_clips, start=1):
        filters.append(f"[{i}:a]adelay={max(0, start_ms)}:all=1[d{i}]")
        labels.append(f"[d{i}]")
    filters.append(
        "".join(labels)
        + f"amix=inputs={len(labels)}:duration=first:normalize=0,"
        + "alimiter=limit=0.98[mix]"
    )

    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", "[mix]",
        "-acodec", "pcm_s16le",
        "-ar", "44100",
        "-ac", "2",
        wav_out,
    ]
    return cmd


def escape_ass_filter_path(path: str) -> str:
    """Escape a filename for use inside ffmpeg's ``ass=`` filter argument."""
    # ffmpeg filter args: '\' and ':' must be escaped; quotes for safety.
    return (
        path.replace("\\", "/")
        .replace(":", "\\:")
        .replace("'", "\\'")
    )


def build_mux_cmd(
    settings: Settings,
    source_video: str,
    mixed_wav: str,
    output_mp4: str,
    ass_path: str | None = None,
) -> list[str]:
    """Replace the source audio with the dub mix; optionally burn subtitles.

    Without subtitles the video stream is copied; burning requires a
    re-encode.
    """
    cmd = [
        _ffmpeg(settings), "-y", "-nostdin",
        "-i", source_video,
        "-i", mixed_wav,
        "-map", "0:v:0",
        "-map", "1:a:0",
    ]
    if ass_path is None:
        cmd += ["-c:v", "copy"]
    else:
        cmd += [
            "-vf", f"ass='{escape_ass_filter_path(ass_path)}'",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
        ]
    cmd += [
        "-c:a", "aac",
        "-b:a", "256k",
        "-ar", "48000",
        "-movflags", "+faststart",
        "-shortest",
        output_mp4,
    ]
    return cmd


# --- probe parsing / validation (pure) -----------------------------------------


def parse_probe_output(payload: str, size_bytes: int) -> MediaInfo:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise PipelineError(errors.PROBE_FAILED, "ffprobe returned invalid JSON") from exc

    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    try:
        duration = float(fmt.get("duration", 0.0))
    except (TypeError, ValueError):
        duration = 0.0
    names = frozenset(
        n.strip().lower() for n in str(fmt.get("format_name", "")).split(",") if n.strip()
    )
    return MediaInfo(
        duration_seconds=duration,
        size_bytes=size_bytes,
        format_names=names,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
        has_video=any(s.get("codec_type") == "video" for s in streams),
    )


def validate_source(info: MediaInfo, settings: Settings) -> None:
    """MVP guardrails: container allow-list, <= 30 min, <= 500 MB, has audio."""
    if info.size_bytes > settings.max_source_bytes:
        raise PipelineError(
            errors.SOURCE_TOO_LARGE,
            f"source is {info.size_bytes} bytes; limit is {settings.max_source_bytes}",
        )
    allowed = settings.allowed_source_container_set
    if not (info.format_names & allowed):
        raise PipelineError(
            errors.SOURCE_UNSUPPORTED_CONTAINER,
            f"container {sorted(info.format_names)} not in {sorted(allowed)}",
        )
    if info.duration_seconds <= 0:
        raise PipelineError(errors.PROBE_FAILED, "could not determine media duration")
    if info.duration_seconds > settings.max_source_duration_seconds:
        raise PipelineError(
            errors.SOURCE_TOO_LONG,
            f"duration {info.duration_seconds:.1f}s exceeds "
            f"{settings.max_source_duration_seconds:.0f}s limit",
        )
    if not info.has_audio:
        raise PipelineError(errors.SOURCE_NO_AUDIO, "source has no audio stream")


# --- execution ----------------------------------------------------------------


async def run_command(
    cmd: list[str],
    *,
    error_code: str,
    heartbeat: HeartbeatFn | None = None,
    heartbeat_seconds: float = 20.0,
    timeout_seconds: float | None = None,
) -> str:
    """Run a subprocess, returning stdout text.

    While the process runs, ``heartbeat`` is awaited every
    ``heartbeat_seconds``; if it raises :class:`JobCancelled` the process is
    killed and the cancellation propagates.
    """
    program = PurePath(cmd[0]).name
    # On Linux, Windows paths keep backslashes as part of the name.
    if "\\" in program or program.lower().endswith(".exe"):
        program = program.rsplit("\\", 1)[-1]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        # Last-chance retry when a host Windows path leaked into the command.
        fallback = "ffprobe" if "ffprobe" in program.lower() else "ffmpeg"
        if cmd[0] != fallback and shutil.which(fallback):
            logger.warning("retrying %s via PATH binary %s", cmd[0], fallback)
            cmd = [fallback, *cmd[1:]]
            program = fallback
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError as retry_exc:
                raise PipelineError(
                    error_code, f"{program} is not installed or not on PATH"
                ) from retry_exc
        else:
            raise PipelineError(
                error_code, f"{program} is not installed or not on PATH"
            ) from exc

    async def _communicate() -> tuple[bytes, bytes]:
        return await proc.communicate()

    comm_task = asyncio.ensure_future(_communicate())
    elapsed = 0.0
    try:
        while True:
            try:
                stdout, stderr = await asyncio.wait_for(
                    asyncio.shield(comm_task), timeout=heartbeat_seconds
                )
                break
            except asyncio.TimeoutError:
                elapsed += heartbeat_seconds
                if timeout_seconds is not None and elapsed >= timeout_seconds:
                    proc.kill()
                    await comm_task
                    raise PipelineError(
                        error_code,
                        f"{program} timed out after {int(elapsed)}s",
                        retryable=True,
                    )
                if heartbeat is not None:
                    await heartbeat()
    except JobCancelled:
        proc.kill()
        await comm_task
        raise
    except asyncio.CancelledError:
        proc.kill()
        await comm_task
        raise

    if proc.returncode != 0:
        tail = stderr.decode("utf-8", errors="replace")[-_STDERR_TAIL_CHARS:]
        logger.error("%s failed (rc=%s): %s", program, proc.returncode, tail)
        raise PipelineError(
            error_code, f"{program} exited with code {proc.returncode}: {tail[-300:]}"
        )
    return stdout.decode("utf-8", errors="replace")
