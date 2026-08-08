"""V2 mix helpers: duck original audio under dubbed spans (no Demucs bed)."""

from __future__ import annotations

from .media import speech_mask_expression
from ..config import Settings


def build_original_duck_bed_cmd(
    settings: Settings,
    original_wav: str,
    ranges_ms: list[tuple[int, int]],
    wav_out: str,
    *,
    duck_level: float = 0.04,
) -> list[str]:
    """Lower original volume only inside ``ranges_ms``; keep everything else.

    Non-source-language speech and BGM/SFX outside the ranges stay intact.
    ``duck_level`` is linear gain inside dubbed windows (≈ -28 dB at 0.04).
    """
    mask = speech_mask_expression(ranges_ms)
    duck = max(0.0, min(1.0, float(duck_level)))
    # volume = 1 outside mask, duck inside mask
    volume_expr = f"1-({mask})*(1-{duck:.6f})"
    filters = (
        f"[0:a]aresample=44100,volume=eval=frame:volume='{volume_expr}'[bed]"
    )
    return [
        settings.ffmpeg_path,
        "-y",
        "-nostdin",
        "-i",
        original_wav,
        "-filter_complex",
        filters,
        "-map",
        "[bed]",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "44100",
        "-ac",
        "2",
        wav_out,
    ]
