"""ASS subtitle generation (pure text; burned in by ffmpeg's ass filter)."""

from __future__ import annotations

from typing import Any, Literal

SubtitleMode = Literal["none", "source", "target"]

_ASS_HEADER = """[Script Info]
Title: Dubby subtitles
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Noto Sans,64,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,3,1,2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def format_ass_time(ms: int) -> str:
    """Milliseconds -> ``H:MM:SS.CC`` (centisecond precision, ASS spec)."""
    if ms < 0:
        ms = 0
    cs = round(ms / 10)
    hours, cs = divmod(cs, 360_000)
    minutes, cs = divmod(cs, 6_000)
    seconds, cs = divmod(cs, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{cs:02d}"


def escape_ass_text(text: str) -> str:
    """Neutralize ASS override blocks and encode newlines."""
    text = text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
    return text.replace("\r\n", "\\N").replace("\n", "\\N").replace("\r", "\\N")


def build_ass(segments: list[dict[str, Any]], mode: SubtitleMode) -> str | None:
    """Render an ASS document, or None when subtitles are disabled.

    ``segments``: rows with start_ms / end_ms / source_text / target_text.
    """
    if mode == "none":
        return None
    field = "source_text" if mode == "source" else "target_text"
    # Target subtitles sit above any source subtitles already embedded in the
    # original picture. The source video pixels themselves are never altered
    # except for this single requested overlay during final rendering.
    margin_v = 160 if mode == "target" else 50
    lines = [_ASS_HEADER.format(margin_v=margin_v)]
    for seg in segments:
        text = str(seg.get(field) or "").strip()
        if not text:
            continue
        start = format_ass_time(int(seg["start_ms"]))
        end = format_ass_time(int(seg["end_ms"]))
        lines.append(
            f"Dialogue: 0,{start},{end},Default,,0,0,0,,{escape_ass_text(text)}\n"
        )
    return "".join(lines)
