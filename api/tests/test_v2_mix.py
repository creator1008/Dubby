"""Unit tests for V2 original-duck mix helper."""

from __future__ import annotations

from app.config import Settings
from app.worker.v2_mix import build_original_duck_bed_cmd


def test_duck_bed_cmd_includes_volume_expression() -> None:
    settings = Settings(ffmpeg_path="ffmpeg")
    cmd = build_original_duck_bed_cmd(
        settings,
        "original.wav",
        [(1000, 2000), (5000, 7000)],
        "ducked.wav",
        duck_level=0.04,
    )
    assert cmd[0] == "ffmpeg"
    assert "original.wav" in cmd
    assert "ducked.wav" in cmd
    joined = " ".join(cmd)
    assert "volume=eval=frame" in joined
    assert "0.040000" in joined
