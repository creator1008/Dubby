"""Pipeline engines: the real media/AI toolchain and a mock stand-in.

The orchestrator in :mod:`.pipeline` is engine-agnostic. ``RealEngine``
shells out to ffmpeg/ffprobe/Demucs and calls OpenAI/ElevenLabs.
``MockEngine`` is a deterministic, dependency-free replacement used ONLY
when ``PIPELINE_MODE=mock`` (development and tests); production refuses to
start in mock mode (enforced in :mod:`app.config`).
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import wave
from abc import ABC, abstractmethod
from pathlib import Path

from ..config import Settings
from . import errors, media, stems
from .elevenlabs_client import ElevenLabsClient
from .errors import PipelineError
from .media import HeartbeatFn, MediaInfo
from .openai_client import OpenAIClient, SegmentDraft

logger = logging.getLogger("dubby.worker.engine")


class Engine(ABC):
    """Every step the orchestrator delegates to external tools/services."""

    #: file extension produced by :meth:`tts`
    tts_extension: str = "mp3"

    @abstractmethod
    async def probe(self, source_path: str, size_bytes: int) -> MediaInfo: ...

    @abstractmethod
    async def extract_audio(self, source_path: str, wav_out: str) -> None: ...

    @abstractmethod
    async def extract_asr_audio(self, source_path: str, audio_out: str) -> None: ...

    @abstractmethod
    async def transcribe(
        self, asr_audio_path: str, language: str
    ) -> list[SegmentDraft]: ...

    @abstractmethod
    async def translate_batch(
        self, items: list[tuple[int, str, float]], source_lang: str, target_lang: str
    ) -> dict[int, str]: ...

    @abstractmethod
    async def adjust_translation(
        self, text: str, target_lang: str, target_seconds: float, direction: str
    ) -> str: ...

    @abstractmethod
    async def split_stems(self, wav_in: str, out_dir: str) -> tuple[str, str]:
        """Returns (vocals_path, no_vocals_path)."""

    @abstractmethod
    async def prepare_voice(
        self,
        vocals_path: str,
        scratch: str,
        name: str,
        ranges_ms: list[tuple[int, int]] | None = None,
    ) -> str:
        """Voice id used for TTS (cloned or configured)."""

    @abstractmethod
    async def cleanup_voice(self, voice_id: str) -> None: ...

    @abstractmethod
    async def tts(
        self,
        text: str,
        voice_id: str,
        out_path: str,
        tone_style: str = "neutral",
        language: str = "",
    ) -> None: ...

    @abstractmethod
    async def clip_duration_seconds(self, path: str) -> float: ...

    @abstractmethod
    async def fit_clip(
        self,
        clip_in: str,
        wav_out: str,
        tempo_factor: float,
        backend: str = "atempo",
        max_seconds: float | None = None,
        gain_db: float = 0.0,
    ) -> None: ...

    @abstractmethod
    async def measure_segment_loudness(
        self,
        vocals_path: str,
        start_ms: int,
        end_ms: int,
    ) -> float: ...

    @abstractmethod
    async def remove_recognized_speech(
        self,
        original_wav: str,
        no_vocals_wav: str,
        ranges_ms: list[tuple[int, int]],
        wav_out: str,
    ) -> None: ...

    @abstractmethod
    async def mix(
        self,
        background_wav: str,
        placed_clips: list[tuple[str, int]],
        wav_out: str,
    ) -> None: ...

    @abstractmethod
    async def mux(
        self,
        source_video: str,
        mixed_wav: str,
        output_mp4: str,
        ass_path: str | None,
    ) -> None: ...


def create_engine(settings: Settings, heartbeat: HeartbeatFn | None = None) -> Engine:
    if settings.pipeline_mode == "mock":
        logger.warning("PIPELINE_MODE=mock: using the mock pipeline engine")
        return MockEngine(settings)
    return RealEngine(settings, heartbeat)


# --- real -----------------------------------------------------------------------


class RealEngine(Engine):
    tts_extension = "mp3"

    def __init__(self, settings: Settings, heartbeat: HeartbeatFn | None = None) -> None:
        self._settings = settings
        self._heartbeat = heartbeat
        self._openai: OpenAIClient | None = None
        self._elevenlabs: ElevenLabsClient | None = None

    @property
    def openai(self) -> OpenAIClient:
        if self._openai is None:
            self._openai = OpenAIClient(self._settings)
        return self._openai

    @property
    def elevenlabs(self) -> ElevenLabsClient:
        if self._elevenlabs is None:
            self._elevenlabs = ElevenLabsClient(self._settings)
        return self._elevenlabs

    async def _run(
        self, cmd: list[str], error_code: str, timeout: float | None = None
    ) -> str:
        return await media.run_command(
            cmd,
            error_code=error_code,
            heartbeat=self._heartbeat,
            heartbeat_seconds=self._settings.pipeline_heartbeat_seconds,
            timeout_seconds=timeout,
        )

    async def probe(self, source_path: str, size_bytes: int) -> MediaInfo:
        out = await self._run(
            media.build_probe_cmd(self._settings, source_path), errors.PROBE_FAILED
        )
        return media.parse_probe_output(out, size_bytes)

    async def extract_audio(self, source_path: str, wav_out: str) -> None:
        await self._run(
            media.build_audio_extract_cmd(self._settings, source_path, wav_out),
            errors.FFMPEG_FAILED,
        )

    async def extract_asr_audio(self, source_path: str, audio_out: str) -> None:
        await self._run(
            media.build_asr_audio_cmd(self._settings, source_path, audio_out),
            errors.FFMPEG_FAILED,
        )

    async def transcribe(
        self, asr_audio_path: str, language: str
    ) -> list[SegmentDraft]:
        return await self.openai.transcribe(asr_audio_path, language)

    async def translate_batch(
        self, items: list[tuple[int, str, float]], source_lang: str, target_lang: str
    ) -> dict[int, str]:
        return await self.openai.translate_batch(items, source_lang, target_lang)

    async def adjust_translation(
        self, text: str, target_lang: str, target_seconds: float, direction: str
    ) -> str:
        return await self.openai.adjust_translation(
            text, target_lang, target_seconds, direction
        )

    async def split_stems(self, wav_in: str, out_dir: str) -> tuple[str, str]:
        await self._run(
            stems.build_demucs_cmd(self._settings, wav_in, out_dir),
            errors.DEMUCS_FAILED,
        )
        vocals, no_vocals = stems.locate_stems(self._settings, wav_in, out_dir)
        return str(vocals), str(no_vocals)

    async def prepare_voice(
        self,
        vocals_path: str,
        scratch: str,
        name: str,
        ranges_ms: list[tuple[int, int]] | None = None,
    ) -> str:
        if self._settings.elevenlabs_voice_id:
            return self._settings.elevenlabs_voice_id
        sample = str(Path(scratch) / f"voice_sample_{name[-24:]}.mp3")
        await self._run(
            media.build_voice_sample_cmd(
                self._settings,
                vocals_path,
                sample,
                self._settings.voice_clone_sample_seconds,
                ranges_ms,
            ),
            errors.FFMPEG_FAILED,
        )
        return await self.elevenlabs.create_voice(sample, name)

    async def cleanup_voice(self, voice_id: str) -> None:
        # Never delete a voice we did not create.
        if voice_id and voice_id != self._settings.elevenlabs_voice_id:
            await self.elevenlabs.delete_voice(voice_id)

    async def tts(
        self,
        text: str,
        voice_id: str,
        out_path: str,
        tone_style: str = "neutral",
        language: str = "",
    ) -> None:
        await self.elevenlabs.tts_to_file(
            text, voice_id, out_path, tone_style, language
        )

    async def clip_duration_seconds(self, path: str) -> float:
        out = await self._run(
            media.build_probe_cmd(self._settings, path), errors.PROBE_FAILED
        )
        info = media.parse_probe_output(out, 0)
        return info.duration_seconds

    async def fit_clip(
        self,
        clip_in: str,
        wav_out: str,
        tempo_factor: float,
        backend: str = "atempo",
        max_seconds: float | None = None,
        gain_db: float = 0.0,
    ) -> None:
        await self._run(
            media.build_clip_fit_cmd(
                self._settings,
                clip_in,
                wav_out,
                tempo_factor,
                backend=backend,
                max_seconds=max_seconds,
                gain_db=gain_db,
            ),
            errors.FFMPEG_FAILED,
        )

    async def measure_segment_loudness(
        self,
        vocals_path: str,
        start_ms: int,
        end_ms: int,
    ) -> float:
        return await asyncio.to_thread(
            media.measure_pcm16_wav_db,
            vocals_path,
            start_ms,
            end_ms,
        )

    async def remove_recognized_speech(
        self,
        original_wav: str,
        no_vocals_wav: str,
        ranges_ms: list[tuple[int, int]],
        wav_out: str,
    ) -> None:
        await self._run(
            media.build_selective_voice_removal_cmd(
                self._settings,
                original_wav,
                no_vocals_wav,
                ranges_ms,
                wav_out,
            ),
            errors.FFMPEG_FAILED,
        )

    async def mix(
        self,
        background_wav: str,
        placed_clips: list[tuple[str, int]],
        wav_out: str,
    ) -> None:
        await self._run(
            media.build_mix_cmd(self._settings, background_wav, placed_clips, wav_out),
            errors.FFMPEG_FAILED,
        )

    async def mux(
        self,
        source_video: str,
        mixed_wav: str,
        output_mp4: str,
        ass_path: str | None,
    ) -> None:
        await self._run(
            media.build_mux_cmd(
                self._settings, source_video, mixed_wav, output_mp4, ass_path
            ),
            errors.FFMPEG_FAILED,
        )


# --- mock -----------------------------------------------------------------------

_MOCK_RATE = 8000
_MOCK_DEFAULT_DURATION = 8.0
# Rough speaking pace used to give mock TTS clips text-dependent durations.
_MOCK_SECONDS_PER_CHAR = 0.05


def _write_wav(path: str, seconds: float, rate: int = _MOCK_RATE) -> None:
    frames = max(1, int(round(seconds * rate)))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * frames)


def _wav_duration(path: str) -> float:
    with wave.open(path, "rb") as r:
        rate = r.getframerate()
        return r.getnframes() / float(rate) if rate else 0.0


class MockEngine(Engine):
    """Deterministic, offline replacement for every external dependency."""

    tts_extension = "wav"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def probe(self, source_path: str, size_bytes: int) -> MediaInfo:
        if source_path.lower().endswith(".wav"):
            try:
                duration = _wav_duration(source_path)
            except (wave.Error, EOFError) as exc:
                raise PipelineError(errors.PROBE_FAILED, "unreadable wav") from exc
        else:
            duration = _MOCK_DEFAULT_DURATION
        return MediaInfo(
            duration_seconds=duration,
            size_bytes=size_bytes,
            format_names=frozenset({"mp4", "mock"}),
            has_audio=True,
            has_video=True,
        )

    async def extract_audio(self, source_path: str, wav_out: str) -> None:
        _write_wav(wav_out, _MOCK_DEFAULT_DURATION)

    async def extract_asr_audio(self, source_path: str, audio_out: str) -> None:
        _write_wav(audio_out, _MOCK_DEFAULT_DURATION)

    async def transcribe(
        self, asr_audio_path: str, language: str
    ) -> list[SegmentDraft]:
        duration_ms = int(_wav_duration(asr_audio_path) * 1000) or 8000
        count = 3 if duration_ms >= 3000 else 1
        step = duration_ms // count
        return [
            SegmentDraft(
                start_ms=i * step,
                end_ms=(i + 1) * step,
                text=f"Mock segment {i + 1} ({language})",
            )
            for i in range(count)
        ]

    async def translate_batch(
        self, items: list[tuple[int, str, float]], source_lang: str, target_lang: str
    ) -> dict[int, str]:
        return {idx: f"[{target_lang}] {text}" for idx, text, _ in items}

    async def adjust_translation(
        self, text: str, target_lang: str, target_seconds: float, direction: str
    ) -> str:
        del target_lang
        if direction == "compress":
            limit = max(1, int(target_seconds / _MOCK_SECONDS_PER_CHAR))
            return text[:limit]
        return text + " naturally"

    async def split_stems(self, wav_in: str, out_dir: str) -> tuple[str, str]:
        duration = _wav_duration(wav_in)
        vocals = str(Path(out_dir) / "vocals.wav")
        no_vocals = str(Path(out_dir) / "no_vocals.wav")
        _write_wav(vocals, duration)
        _write_wav(no_vocals, duration)
        return vocals, no_vocals

    async def prepare_voice(
        self,
        vocals_path: str,
        scratch: str,
        name: str,
        ranges_ms: list[tuple[int, int]] | None = None,
    ) -> str:
        del vocals_path, scratch, ranges_ms
        return f"mock-voice-{name[-16:]}"

    async def cleanup_voice(self, voice_id: str) -> None:
        return None

    async def tts(
        self,
        text: str,
        voice_id: str,
        out_path: str,
        tone_style: str = "neutral",
        language: str = "",
    ) -> None:
        del voice_id, tone_style, language
        seconds = max(0.2, len(text) * _MOCK_SECONDS_PER_CHAR)
        _write_wav(out_path, seconds)

    async def clip_duration_seconds(self, path: str) -> float:
        return _wav_duration(path)

    async def fit_clip(
        self,
        clip_in: str,
        wav_out: str,
        tempo_factor: float,
        backend: str = "atempo",
        max_seconds: float | None = None,
        gain_db: float = 0.0,
    ) -> None:
        del backend, gain_db
        duration = _wav_duration(clip_in) / max(0.01, tempo_factor)
        if max_seconds is not None:
            duration = min(duration, max_seconds)
        _write_wav(wav_out, duration)

    async def measure_segment_loudness(
        self,
        vocals_path: str,
        start_ms: int,
        end_ms: int,
    ) -> float:
        del vocals_path, start_ms, end_ms
        return -20.0

    async def remove_recognized_speech(
        self,
        original_wav: str,
        no_vocals_wav: str,
        ranges_ms: list[tuple[int, int]],
        wav_out: str,
    ) -> None:
        del no_vocals_wav, ranges_ms
        shutil.copyfile(original_wav, wav_out)

    async def mix(
        self,
        background_wav: str,
        placed_clips: list[tuple[str, int]],
        wav_out: str,
    ) -> None:
        _write_wav(wav_out, _wav_duration(background_wav))

    async def mux(
        self,
        source_video: str,
        mixed_wav: str,
        output_mp4: str,
        ass_path: str | None,
    ) -> None:
        shutil.copyfile(source_video, output_mp4)
