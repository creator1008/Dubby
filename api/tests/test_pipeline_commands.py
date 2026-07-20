"""Command-construction tests for ffmpeg/ffprobe/Demucs (no binaries needed)."""

from __future__ import annotations

import sys
import wave
from array import array

from app.config import Settings
from app.worker import media, stems


def _settings(**kw) -> Settings:
    defaults = {"ffmpeg_path": "ffmpeg", "ffprobe_path": "ffprobe"}
    return Settings(_env_file=None, **{**defaults, **kw})


def test_probe_cmd_shape() -> None:
    cmd = media.build_probe_cmd(_settings(), "in.mp4")
    assert cmd[0] == "ffprobe"
    assert "-show_format" in cmd and "-show_streams" in cmd
    assert cmd[-1] == "in.mp4"


def test_probe_cmd_respects_configured_path() -> None:
    cmd = media.build_probe_cmd(_settings(ffprobe_path="/opt/ffprobe"), "x.mp4")
    assert cmd[0] == "/opt/ffprobe"


def test_audio_extract_cmd_is_stereo_pcm_44100() -> None:
    cmd = media.build_audio_extract_cmd(_settings(), "src.mp4", "out.wav")
    assert cmd[0] == "ffmpeg"
    assert "-vn" in cmd
    i = cmd.index("-acodec")
    assert cmd[i + 1] == "pcm_s16le"
    assert cmd[cmd.index("-ar") + 1] == "44100"
    assert cmd[cmd.index("-ac") + 1] == "2"
    assert cmd[-1] == "out.wav"


def test_asr_audio_cmd_is_mono_16k_mp3() -> None:
    cmd = media.build_asr_audio_cmd(_settings(), "src.mp4", "asr.mp3")
    assert cmd[cmd.index("-acodec") + 1] == "libmp3lame"
    assert cmd[cmd.index("-ar") + 1] == "16000"
    assert cmd[cmd.index("-ac") + 1] == "1"


def test_clip_fit_cmd_without_speedup_has_no_filter() -> None:
    cmd = media.build_clip_fit_cmd(_settings(), "seg.mp3", "seg.wav", 1.0)
    assert "-filter:a" not in cmd


def test_clip_fit_cmd_chains_atempo_beyond_two() -> None:
    cmd = media.build_clip_fit_cmd(_settings(), "seg.mp3", "seg.wav", 2.5)
    filt = cmd[cmd.index("-filter:a") + 1]
    assert filt.startswith("atempo=2.0,atempo=1.25")


def test_clip_fit_cmd_rubberband_and_duration_cap() -> None:
    cmd = media.build_clip_fit_cmd(
        _settings(), "seg.mp3", "seg.wav", 1.8, backend="rubberband", max_seconds=2.5
    )
    assert cmd[cmd.index("-filter:a") + 1].startswith("rubberband=tempo=1.8")
    assert cmd[cmd.index("-t") + 1] == "2.500"


def test_clip_fit_cmd_applies_source_relative_gain() -> None:
    cmd = media.build_clip_fit_cmd(
        _settings(), "seg.mp3", "seg.wav", 1.0, gain_db=3.25
    )
    assert cmd[cmd.index("-filter:a") + 1] == "volume=3.25dB"


def test_pcm16_segment_loudness_tracks_amplitude(tmp_path) -> None:
    wav_path = tmp_path / "levels.wav"
    with wave.open(str(wav_path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(1000)
        target.writeframes(array("h", [1000] * 1000 + [10000] * 1000).tobytes())

    quiet = media.measure_pcm16_wav_db(str(wav_path), 0, 1000)
    loud = media.measure_pcm16_wav_db(str(wav_path), 1000, 2000)
    assert loud > quiet + 15


def test_voice_sample_cmd_trims_to_sample_seconds() -> None:
    cmd = media.build_voice_sample_cmd(_settings(), "vocals.wav", "s.mp3", 45.0)
    assert cmd[cmd.index("-t") + 1] == "45"


def test_selective_voice_removal_uses_only_asr_ranges() -> None:
    cmd = media.build_selective_voice_removal_cmd(
        _settings(),
        "original.wav",
        "no_vocals.wav",
        [(1000, 3000), (5000, 5500)],
        "speech_removed.wav",
    )

    assert cmd.count("-i") == 2
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "lt(t,0.780000)" in graph
    assert "(t-0.780000)/0.060000" in graph
    assert "lt(t,3.080000),1" in graph
    assert "lt(t,4.780000)" in graph
    assert "[original][removed]amix=inputs=2" in graph
    assert cmd[-1] == "speech_removed.wav"


def test_mix_cmd_places_and_sums_clips() -> None:
    cmd = media.build_mix_cmd(
        _settings(),
        "no_vocals.wav",
        [("a.wav", 0), ("b.wav", 1500)],
        "mix.wav",
    )
    assert cmd.count("-i") == 3  # background + 2 clips
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "[1:a]adelay=0:all=1[d1]" in graph
    assert "[2:a]adelay=1500:all=1[d2]" in graph
    assert "amix=inputs=3:duration=first:normalize=0" in graph
    assert "alimiter" in graph
    assert cmd[cmd.index("-map") + 1] == "[mix]"


def test_mux_cmd_copies_video_without_subtitles() -> None:
    cmd = media.build_mux_cmd(_settings(), "src.mp4", "mix.wav", "out.mp4", None)
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert "-vf" not in cmd
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert "-shortest" in cmd
    # video from input 0, audio from input 1
    maps = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
    assert maps == ["0:v:0", "1:a:0"]


def test_mux_cmd_burns_subtitles_with_reencode() -> None:
    cmd = media.build_mux_cmd(
        _settings(), "src.mp4", "mix.wav", "out.mp4", "/tmp/subs.ass"
    )
    vf = cmd[cmd.index("-vf") + 1]
    assert vf.startswith("ass='")
    assert cmd[cmd.index("-c:v") + 1] == "libx264"


def test_ass_filter_path_escaping_windows() -> None:
    escaped = media.escape_ass_filter_path("C:\\scratch\\subs.ass")
    assert escaped == "C\\:/scratch/subs.ass"


def test_demucs_cmd_two_stems_model_device() -> None:
    settings = _settings(demucs_model="mdx_extra", demucs_device="cuda", demucs_jobs=2)
    cmd = stems.build_demucs_cmd(settings, "audio.wav", "outdir")
    assert cmd[:3] == [sys.executable, "-m", "demucs.separate"]
    assert cmd[cmd.index("-n") + 1] == "mdx_extra"
    assert cmd[cmd.index("--two-stems") + 1] == "vocals"
    assert cmd[cmd.index("-d") + 1] == "cuda"
    assert cmd[cmd.index("-j") + 1] == "2"
    assert cmd[cmd.index("-o") + 1] == "outdir"
    assert cmd[-1] == "audio.wav"


def test_demucs_stem_location(tmp_path) -> None:
    settings = _settings()
    stem_dir = tmp_path / "htdemucs_ft" / "audio"
    stem_dir.mkdir(parents=True)
    (stem_dir / "vocals.wav").write_bytes(b"x")
    (stem_dir / "no_vocals.wav").write_bytes(b"x")
    vocals, no_vocals = stems.locate_stems(settings, "audio.wav", str(tmp_path))
    assert vocals.name == "vocals.wav" and no_vocals.name == "no_vocals.wav"


def test_demucs_missing_stems_raises(tmp_path) -> None:
    import pytest

    from app.worker.errors import PipelineError

    with pytest.raises(PipelineError) as exc:
        stems.locate_stems(_settings(), "audio.wav", str(tmp_path))
    assert exc.value.code == "demucs_failed"
