"""Local-only verification server for pipeline steps 1 and 2.

Run from ``api``:

    uvicorn app.local_step12:app --reload --port 8002

It accepts a raw media body, extracts a high-quality WAV and an ASR WAV,
transcribes actual speech with faster-whisper word timestamps, and exports
one WAV clip per timestamp/text pair. No Supabase, R2, or authentication is
involved. The server intentionally binds only through the uvicorn command
chosen by the developer and must not be deployed publicly.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import Annotated
from uuid import uuid4

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from .worker.elevenlabs_client import tts_model_for_language

REPO_ROOT = Path(__file__).resolve().parents[2]
# The user-facing local setup stores provider keys in the repository root.
# api/.env remains supported and takes precedence when both exist.
load_dotenv(REPO_ROOT / ".env")
load_dotenv(REPO_ROOT / "api" / ".env", override=True)

MAX_SOURCE_BYTES = 500 * 1024 * 1024
DATA_ROOT = REPO_ROOT / ".local-data" / "step12"
SUPPORTED_LANGUAGES = {"ko", "en", "vi"}
INITIAL_PROMPTS = {
    "ko": "정확한 한국어 받아쓰기입니다. 고유명사, 띄어쓰기, 문장 부호를 정확히 표기합니다.",
    "en": "This is an accurate English transcript with correct names and punctuation.",
    "vi": "Đây là bản chép lời tiếng Việt chính xác với tên riêng và dấu câu.",
}
SENTENCE_END_RE = re.compile(r"[.!?。？！][\"'”’)]*$")


@dataclass(frozen=True)
class TimedWord:
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class SpeechPair:
    idx: int
    start_ms: int
    end_ms: int
    text: str
    target_text: str
    speaker_id: str | None
    audio_path: str
    audio_url: str


class DubSegment(BaseModel):
    idx: int
    target_text: str = Field(min_length=1, max_length=2000)


class DubVoiceRequest(BaseModel):
    run_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    segments: list[DubSegment] = Field(min_length=1, max_length=500)
    tone_style: str = Field(default="neutral", pattern="^(neutral|warm|energetic|serious)$")


class RenderSegment(BaseModel):
    idx: int
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    source_text: str = Field(default="", max_length=2000)
    target_text: str = Field(default="", max_length=2000)


class RenderDubRequest(BaseModel):
    run_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    segments: list[RenderSegment] = Field(min_length=1, max_length=500)
    subtitle_mode: str = Field(default="none", pattern="^(none|source|target)$")


def _ffmpeg_executable() -> str:
    configured = os.getenv("FFMPEG_PATH", "").strip()
    if configured:
        return configured
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg  # type: ignore[import-not-found]

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as exc:
        raise RuntimeError(
            "FFmpeg를 찾을 수 없습니다. FFmpeg를 설치하거나 "
            "FFMPEG_PATH를 설정해 주세요."
        ) from exc


def _run_ffmpeg(args: list[str]) -> None:
    command = [_ffmpeg_executable(), "-y", "-nostdin", *args]
    result = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"FFmpeg 실패: {result.stderr[-600:]}")


def _run_command(command: list[str], label: str) -> None:
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"{label} 실패: {result.stderr[-1000:]}")


def _ffprobe_executable() -> str:
    configured = os.getenv("FFPROBE_PATH", "").strip()
    if configured:
        return configured
    found = shutil.which("ffprobe")
    if found:
        return found
    ffmpeg = Path(_ffmpeg_executable())
    sibling = ffmpeg.with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
    if sibling.is_file():
        return str(sibling)
    raise RuntimeError("FFprobe를 찾을 수 없습니다. FFPROBE_PATH를 설정해 주세요.")


def _audio_duration(path: Path) -> float:
    result = subprocess.run(
        [
            _ffprobe_executable(),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"더빙 음성 길이 측정 실패: {result.stderr[-400:]}")
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError("더빙 음성 길이를 확인할 수 없습니다.") from exc


def _atempo_filters(factor: float) -> list[str]:
    if factor <= 0:
        return []
    filters: list[str] = []
    remaining = factor
    while remaining > 2:
        filters.append("atempo=2")
        remaining /= 2
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    if abs(remaining - 1) > 0.001:
        filters.append(f"atempo={remaining:.6f}")
    return filters


def _merge_speech_ranges(
    ranges_ms: list[tuple[int, int]],
    *,
    max_gap_ms: int = 0,
) -> list[tuple[int, int]]:
    """Normalize overlapping ASR ranges used by selective voice removal."""
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


def _speech_mask_expression(
    ranges_ms: list[tuple[int, int]],
    fade_seconds: float = 0.06,
    leading_padding_seconds: float = 0.16,
    trailing_padding_seconds: float = 0.08,
) -> str:
    """Return an FFmpeg volume expression active only inside ASR speech."""
    masks: list[str] = []
    for start_ms, end_ms in _merge_speech_ranges(ranges_ms):
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


def _cover_recognized_phrase_boundaries(
    ranges_ms: list[tuple[int, int]],
    segments: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Cover words omitted at the start or end of an ASR phrase.

    Local forced alignment is precise around sobbing gaps but can omit a
    short initial or final word. Broader OpenAI segment boundaries supply
    those missing edges without filling pauses in the middle of the phrase.
    """
    adjusted = list(_merge_speech_ranges(ranges_ms))
    for segment_start, segment_end in sorted(segments):
        overlaps = [
            index
            for index, (start, end) in enumerate(adjusted)
            if end > segment_start and start < segment_end
        ]
        if not overlaps:
            # OpenAI can return a valid transcript segment even when the
            # word-level aligner omits every word (short/quiet phrases are
            # especially common). Keeping the segment uncovered leaks the
            # complete source utterance into the dubbed mix, so use the
            # recognized segment itself as the conservative fallback mask.
            if segment_end > segment_start:
                adjusted.append((max(0, segment_start), segment_end))
                adjusted = _merge_speech_ranges(adjusted)
            continue
        first = overlaps[0]
        last = overlaps[-1]
        adjusted[first] = (
            min(adjusted[first][0], segment_start),
            adjusted[first][1],
        )
        adjusted[last] = (
            adjusted[last][0],
            max(adjusted[last][1], segment_end),
        )
    return _merge_speech_ranges(adjusted)


def _join_words(words: list[TimedWord]) -> str:
    text = "".join(word.text for word in words).strip()
    # Some local models omit leading spaces on tokens.
    if " " not in text and len(words) > 1:
        text = " ".join(word.text.strip() for word in words).strip()
    return re.sub(r"\s+([,.!?。？！])", r"\1", text)


def group_words(
    words: list[TimedWord],
    *,
    gap_ms: int = 650,
    max_duration_ms: int = 9000,
) -> list[tuple[int, int, str]]:
    """Group word timestamps into stable, non-overlapping subtitle phrases."""
    clean = [
        word
        for word in words
        if word.text.strip() and word.end_ms > word.start_ms >= 0
    ]
    if not clean:
        return []

    groups: list[list[TimedWord]] = []
    current: list[TimedWord] = []
    for index, word in enumerate(clean):
        current.append(word)
        next_word = clean[index + 1] if index + 1 < len(clean) else None
        duration = word.end_ms - current[0].start_ms
        long_gap = next_word is not None and next_word.start_ms - word.end_ms >= gap_ms
        sentence_end = bool(SENTENCE_END_RE.search(word.text.strip()))
        if next_word is None or long_gap or sentence_end or duration >= max_duration_ms:
            groups.append(current)
            current = []

    result: list[tuple[int, int, str]] = []
    for group in groups:
        start = group[0].start_ms
        end = group[-1].end_ms
        text = _join_words(group)
        if not text:
            continue
        # Ensure rounding or overlapping model tokens never create overlap.
        if result and start < result[-1][1]:
            start = result[-1][1]
        if end > start:
            result.append((start, end, text))
    return result


@lru_cache(maxsize=1)
def _whisper_model() -> object:
    try:
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "로컬 음성인식 모듈이 없습니다. api 폴더에서 "
            "`pip install -e \".[local]\"`을 실행해 주세요."
        ) from exc

    model_name = os.getenv("LOCAL_WHISPER_MODEL", "medium")
    device = os.getenv("LOCAL_WHISPER_DEVICE", "cpu")
    compute_type = os.getenv(
        "LOCAL_WHISPER_COMPUTE_TYPE",
        "int8" if device == "cpu" else "float16",
    )
    return WhisperModel(model_name, device=device, compute_type=compute_type)


def _openai_headers() -> dict[str, str]:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY가 .env에 설정되지 않았습니다.")
    return {"Authorization": f"Bearer {key}"}


def _openai_transcribe(
    asr_wav: Path,
    language: str,
) -> tuple[list[tuple[int, int, str]], list[tuple[int, int]]]:
    """Use OpenAI Whisper word timestamps when an API key is configured."""
    base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("WHISPER_MODEL", "whisper-1")
    # No initial prompt here: whisper-1 can echo the prompt verbatim into the
    # transcript on noisy inputs, replacing the actual speech.
    with asr_wav.open("rb") as audio:
        response = httpx.post(
            f"{base}/audio/transcriptions",
            headers=_openai_headers(),
            data={
                "model": model,
                "language": language,
                "response_format": "verbose_json",
                "timestamp_granularities[]": ["word", "segment"],
                "temperature": 0,
            },
            files={"file": (asr_wav.name, audio, "audio/wav")},
            timeout=600,
        )
    if response.status_code >= 400:
        raise RuntimeError(
            f"OpenAI 음성인식 실패 ({response.status_code}): {response.text[:300]}"
        )
    payload = response.json()
    words = [
        TimedWord(
            start_ms=max(0, round(float(word["start"]) * 1000)),
            end_ms=round(float(word["end"]) * 1000),
            text=str(word.get("word", "")),
        )
        for word in payload.get("words") or []
        if word.get("start") is not None and word.get("end") is not None
    ]
    # Whisper's segment boundaries are generally sentence-aware. Keep them
    # rather than globally regrouping words into arbitrary 9-second chunks.
    # Only split an unusually long segment using its own word timestamps.
    drafts: list[tuple[int, int, str]] = []
    for segment in payload.get("segments") or []:
        start = max(0, round(float(segment.get("start", 0)) * 1000))
        end = round(float(segment.get("end", 0)) * 1000)
        text = str(segment.get("text", "")).strip()
        if not text or end <= start:
            continue
        # Standard Whisper heuristic for hallucinated text over non-speech.
        no_speech = float(segment.get("no_speech_prob", 0.0))
        avg_logprob = float(segment.get("avg_logprob", 0.0))
        if no_speech > 0.6 and avg_logprob < -1.0:
            continue
        segment_words = [
            word
            for word in words
            if word.start_ms < end and word.end_ms > start
        ]
        sentence_count = len(re.findall(r"[.!?。？！]", text))
        if segment_words and (end - start > 6500 or sentence_count > 1):
            split = group_words(
                segment_words,
                gap_ms=500,
                max_duration_ms=4500,
            )
            drafts.extend(split or [(start, end, text)])
        else:
            drafts.append((start, end, text))

    if not drafts:
        drafts = group_words(words, gap_ms=500, max_duration_ms=4500)

    non_overlapping: list[tuple[int, int, str]] = []
    for start, end, text in sorted(drafts, key=lambda item: (item[0], item[1])):
        if non_overlapping and start < non_overlapping[-1][1]:
            start = non_overlapping[-1][1]
        if end > start and text:
            non_overlapping.append((start, end, text))
    word_ranges = _merge_speech_ranges(
        [
            (word.start_ms, word.end_ms)
            for word in words
            if word.text.strip() and word.end_ms > word.start_ms
        ],
        # Join only tightly adjacent words. Longer pauses, sobbing, breaths,
        # cheers, and other non-language sounds remain on the original track.
        max_gap_ms=120,
    )
    return non_overlapping, word_ranges or [
        (start, end) for start, end, _ in non_overlapping
    ]


def _openai_diarize(
    asr_wav: Path,
    language: str,
) -> list[tuple[int, int, str, str]]:
    """Return OpenAI speaker turns for local multi-speaker cloning."""
    base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("DIARIZATION_MODEL", "gpt-4o-transcribe-diarize")
    with asr_wav.open("rb") as audio:
        response = httpx.post(
            f"{base}/audio/transcriptions",
            headers=_openai_headers(),
            data={
                "model": model,
                "language": language,
                "response_format": "diarized_json",
                "chunking_strategy": "auto",
            },
            files={"file": (asr_wav.name, audio, "audio/wav")},
            timeout=600,
        )
    if response.status_code >= 400:
        raise RuntimeError(
            f"OpenAI 화자 구분 실패 ({response.status_code}): {response.text[:300]}"
        )
    turns: list[tuple[int, int, str, str]] = []
    for segment in response.json().get("segments") or []:
        start = max(0, round(float(segment.get("start", 0)) * 1000))
        end = round(float(segment.get("end", 0)) * 1000)
        speaker = str(segment.get("speaker") or "").strip()
        text = str(segment.get("text") or "").strip()
        if speaker and end > start:
            turns.append((start, end, speaker, text))
    return turns


def _split_diarized_turns(
    turns: list[tuple[int, int, str, str]],
    max_duration_ms: int = 6000,
) -> list[tuple[int, int, str, str]]:
    """Create subtitle/TTS slots at speaker changes and fixed time intervals."""
    result: list[tuple[int, int, str, str]] = []
    for start, end, speaker, text in turns:
        clean = text.strip()
        duration = end - start
        if not clean or duration <= 0:
            continue
        part_count = max(1, (duration + max_duration_ms - 1) // max_duration_ms)
        words = clean.split()
        part_count = min(part_count, len(words))
        if part_count == 1:
            result.append((start, end, clean, speaker))
            continue

        cursor = 0
        for part_idx in range(part_count):
            remaining_words = len(words) - cursor
            remaining_parts = part_count - part_idx
            take = max(1, round(remaining_words / remaining_parts))
            part_words = words[cursor : cursor + take]
            part_start = start + round(duration * cursor / len(words))
            cursor += take
            part_end = (
                end
                if part_idx == part_count - 1
                else start + round(duration * cursor / len(words))
            )
            result.append((part_start, part_end, " ".join(part_words), speaker))
    return result


def _assign_speaker_ids(
    drafts: list[tuple[int, int, str]],
    turns: list[tuple[int, int, str, str]],
) -> list[str]:
    assigned: list[str] = []
    for start, end, _ in drafts:
        overlap_by_speaker: dict[str, int] = {}
        for turn_start, turn_end, speaker, _ in turns:
            overlap = max(0, min(end, turn_end) - max(start, turn_start))
            if overlap:
                overlap_by_speaker[speaker] = (
                    overlap_by_speaker.get(speaker, 0) + overlap
                )
        assigned.append(
            max(overlap_by_speaker, key=overlap_by_speaker.get)
            if overlap_by_speaker
            else "speaker_0"
        )
    return assigned


def _local_speech_ranges(asr_wav: Path, language: str) -> list[tuple[int, int]]:
    """Precisely align linguistic words while excluding sobbing and pauses."""
    try:
        model = _whisper_model()
    except RuntimeError:
        return []
    segments, _ = model.transcribe(
        str(asr_wav),
        language=language,
        beam_size=1,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={
            "min_silence_duration_ms": 200,
            "speech_pad_ms": 30,
        },
        condition_on_previous_text=False,
    )
    ranges: list[tuple[int, int]] = []
    for segment in segments:
        for word in segment.words or []:
            if word.start is None or word.end is None:
                continue
            probability = getattr(word, "probability", None)
            if probability is not None and float(probability) < 0.2:
                continue
            start = max(0, round(float(word.start) * 1000))
            end = round(float(word.end) * 1000)
            if end - start >= 40 and str(word.word).strip():
                ranges.append((start, end))
    return _merge_speech_ranges(ranges, max_gap_ms=120)


def _transcribe(
    asr_wav: Path,
    language: str,
) -> tuple[list[tuple[int, int, str]], list[tuple[int, int]]]:
    if os.getenv("OPENAI_API_KEY", "").strip():
        drafts, api_ranges = _openai_transcribe(asr_wav, language)
        # OpenAI Whisper can stretch a word timestamp over a long sob or
        # dramatic pause. A second local alignment pass supplies only the
        # acoustic word ranges used for removal; OpenAI remains the source
        # of transcript text and subtitle segmentation.
        aligned_ranges = _local_speech_ranges(asr_wav, language)
        return drafts, aligned_ranges or api_ranges

    model = _whisper_model()
    segments, _ = model.transcribe(
        str(asr_wav),
        language=language,
        beam_size=5,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={
            "min_silence_duration_ms": 200,
            "speech_pad_ms": 30,
        },
        condition_on_previous_text=False,
    )
    words: list[TimedWord] = []
    fallback: list[tuple[int, int, str]] = []
    for segment in segments:
        text = str(segment.text).strip()
        start_ms = max(0, round(float(segment.start) * 1000))
        end_ms = round(float(segment.end) * 1000)
        if text and end_ms > start_ms:
            fallback.append((start_ms, end_ms, text))
        for word in segment.words or []:
            if word.start is None or word.end is None:
                continue
            words.append(
                TimedWord(
                    start_ms=max(0, round(float(word.start) * 1000)),
                    end_ms=round(float(word.end) * 1000),
                    text=str(word.word),
                )
            )
    drafts = group_words(words) or fallback
    word_ranges = _merge_speech_ranges(
        [
            (word.start_ms, word.end_ms)
            for word in words
            if word.text.strip() and word.end_ms > word.start_ms
        ],
        max_gap_ms=120,
    )
    return drafts, word_ranges or [(start, end) for start, end, _ in drafts]


def _translate(
    drafts: list[tuple[int, int, str]],
    source_language: str,
    target_language: str,
) -> list[str]:
    if source_language == target_language:
        return [text for _, _, text in drafts]
    base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("TRANSLATION_MODEL", "gpt-4o-mini")
    response = httpx.post(
        f"{base}/chat/completions",
        headers={**_openai_headers(), "Content-Type": "application/json"},
        json={
            "model": model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"Translate dubbing subtitles from {source_language} to "
                        f"{target_language}. Write natural native spoken language "
                        "for voice-over, retain all required diacritics, and spell "
                        "numbers or abbreviations as they should be spoken. Preserve "
                        "one item per idx; do not merge, split, omit, or reorder. "
                        "Return JSON: "
                        '{"translations":[{"idx":0,"text":"..."}]}.'
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "segments": [
                                {
                                    "idx": idx,
                                    "text": text,
                                    "seconds": round((end - start) / 1000, 2),
                                }
                                for idx, (start, end, text) in enumerate(drafts)
                            ]
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        },
        timeout=300,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"OpenAI 번역 실패 ({response.status_code}): {response.text[:300]}"
        )
    try:
        content = json.loads(response.json()["choices"][0]["message"]["content"])
        by_idx = {
            int(item["idx"]): str(item["text"]).strip()
            for item in content["translations"]
        }
        return [by_idx[idx] for idx in range(len(drafts))]
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("OpenAI 번역 응답 형식이 올바르지 않습니다.") from exc


def _process(
    source: Path,
    work_dir: Path,
    source_language: str,
    target_language: str,
    diarization_enabled: bool,
) -> dict:
    audio_wav = work_dir / "original_audio.wav"
    asr_wav = work_dir / "asr_audio.wav"
    clips_dir = work_dir / "speech"
    clips_dir.mkdir()

    # Step 1: preserve the source audio as stereo PCM for later processing.
    _run_ffmpeg(
        [
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-c:a",
            "pcm_s16le",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(audio_wav),
        ]
    )
    # Separate ASR representation: speech-friendly mono 16 kHz PCM.
    _run_ffmpeg(
        [
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-af",
            "highpass=f=60,lowpass=f=7800",
            "-c:a",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(asr_wav),
        ]
    )

    # Step 2: word timestamps -> sentence/gap grouping -> matching audio clips.
    drafts, speech_ranges = _transcribe(asr_wav, source_language)
    if diarization_enabled:
        turns = _openai_diarize(asr_wav, source_language)
        max_segment_ms = max(
            1000,
            round(float(os.getenv("SPEECH_SEGMENT_MAX_SECONDS", "6")) * 1000),
        )
        diarized = _split_diarized_turns(turns, max_segment_ms)
        if diarized:
            drafts = [(start, end, text) for start, end, text, _ in diarized]
            speaker_ids = [speaker for _, _, _, speaker in diarized]
        else:
            speaker_ids = _assign_speaker_ids(drafts, turns)
    else:
        speaker_ids = ["speaker_0"] * len(drafts)
    translations = _translate(drafts, source_language, target_language)
    pairs: list[SpeechPair] = []
    for idx, (start_ms, end_ms, text) in enumerate(drafts):
        clip_name = f"{idx + 1:04d}_{start_ms}_{end_ms}.wav"
        clip_path = clips_dir / clip_name
        _run_ffmpeg(
            [
                "-ss",
                f"{start_ms / 1000:.3f}",
                "-i",
                str(audio_wav),
                "-t",
                f"{(end_ms - start_ms) / 1000:.3f}",
                "-c:a",
                "pcm_s16le",
                "-ar",
                "16000",
                "-ac",
                "1",
                str(clip_path),
            ]
        )
        pairs.append(
            SpeechPair(
                idx=idx,
                start_ms=start_ms,
                end_ms=end_ms,
                text=text,
                target_text=translations[idx],
                speaker_id=speaker_ids[idx],
                audio_path=f"speech/{clip_name}",
                audio_url=f"/v1/local/step12/{work_dir.name}/speech/{clip_name}",
            )
        )

    manifest = {
        "run_id": work_dir.name,
        "language": source_language,
        "target_language": target_language,
        "asr_provider": "openai" if os.getenv("OPENAI_API_KEY", "").strip() else "local",
        "diarization_enabled": diarization_enabled,
        "source_url": f"/v1/local/step12/{work_dir.name}/{source.name}",
        "audio_path": "original_audio.wav",
        "audio_url": f"/v1/local/step12/{work_dir.name}/original_audio.wav",
        "asr_audio_path": "asr_audio.wav",
        "asr_audio_url": f"/v1/local/step12/{work_dir.name}/asr_audio.wav",
        "speech_ranges": [
            {"start_ms": start, "end_ms": end}
            for start, end in speech_ranges
        ],
        "segments": [asdict(pair) for pair in pairs],
    }
    (work_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def _eleven_headers() -> dict[str, str]:
    key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY가 .env에 설정되지 않았습니다.")
    return {"xi-api-key": key}


def _prepare_voice_sample(work_dir: Path, speaker_id: str | None = None) -> Path:
    """Build the clone sample from the Demucs vocals stem.

    Cutting the sample from the raw mix contaminates the cloned voice with
    background music and ambience, which makes every generated dub sound
    dirty. The vocals stem keeps only the speaker.
    """
    manifest_path = work_dir / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("먼저 오디오·자막 추출을 실행해 주세요.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    all_segments = manifest.get("segments") or []
    segments = [
        segment
        for segment in all_segments
        if speaker_id is None or segment.get("speaker_id") == speaker_id
    ]
    if not segments:
        segments = all_segments
    if not segments:
        raise RuntimeError("보이스 샘플로 사용할 음성 구간이 없습니다.")

    vocals, _ = _separate_no_vocals(work_dir)
    max_seconds = float(os.getenv("VOICE_CLONE_SAMPLE_SECONDS", "60"))
    trims: list[str] = []
    labels: list[str] = []
    total = 0.0
    for index, segment in enumerate(segments):
        start = max(0.0, float(segment["start_ms"]) / 1000)
        end = float(segment["end_ms"]) / 1000
        take = min(end - start, max_seconds - total)
        if take <= 0.05:
            continue
        trims.append(
            f"[0:a]atrim=start={start:.3f}:end={start + take:.3f},"
            f"asetpts=PTS-STARTPTS[s{index}]"
        )
        labels.append(f"[s{index}]")
        total += take
        if total >= max_seconds:
            break
    if not labels:
        raise RuntimeError("보이스 샘플로 사용할 음성 구간이 없습니다.")

    filters = trims + [
        "".join(labels)
        + f"concat=n={len(labels)}:v=0:a=1,"
        + "highpass=f=60,alimiter=limit=0.97[voice]"
    ]
    safe_speaker = re.sub(r"[^A-Za-z0-9_-]+", "_", speaker_id or "default")
    sample = work_dir / f"voice_sample_{safe_speaker}.mp3"
    _run_ffmpeg(
        [
            "-i",
            str(vocals),
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[voice]",
            "-c:a",
            "libmp3lame",
            "-ar",
            "44100",
            "-ac",
            "1",
            "-b:a",
            "192k",
            str(sample),
        ]
    )
    return sample


def _create_eleven_voice(
    work_dir: Path,
    speaker_id: str | None = None,
) -> tuple[str, bool]:
    configured = os.getenv("ELEVENLABS_VOICE_ID", "").strip()
    if configured:
        return configured, False
    base = os.getenv("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io").rstrip("/")
    sample = _prepare_voice_sample(work_dir, speaker_id)
    with sample.open("rb") as audio:
        response = httpx.post(
            f"{base}/v1/voices/add",
            headers=_eleven_headers(),
            data={
                "name": f"Dubby {work_dir.name[:8]} {speaker_id or 'default'}",
                "description": "Temporary local Dubby verification voice",
            },
            files={"files": (sample.name, audio, "audio/mpeg")},
            timeout=300,
        )
    if response.status_code >= 400:
        raise RuntimeError(
            f"ElevenLabs 보이스 클론 실패 ({response.status_code}): "
            f"{response.text[:300]}"
        )
    voice_id = response.json().get("voice_id")
    if not voice_id:
        raise RuntimeError("ElevenLabs 응답에 voice_id가 없습니다.")
    return str(voice_id), True


def _mean_volume_db(path: Path, start_ms: int, end_ms: int) -> float:
    result = subprocess.run(
        [
            _ffmpeg_executable(),
            "-nostdin",
            "-ss",
            f"{start_ms / 1000:.3f}",
            "-i",
            str(path),
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


def _relative_loudness_gains(levels: dict[int, float]) -> dict[int, float]:
    if not levels:
        return {}
    reference = median(levels.values())
    return {
        idx: round(max(-8.0, min(6.0, level - reference)), 2)
        for idx, level in levels.items()
    }


def _source_loudness_levels(
    work_dir: Path,
    segment_indices: set[int],
) -> dict[int, float]:
    manifest = json.loads((work_dir / "manifest.json").read_text(encoding="utf-8"))
    vocals, _ = _separate_no_vocals(work_dir)
    speech_ranges = [
        (int(item["start_ms"]), int(item["end_ms"]))
        for item in manifest.get("speech_ranges") or []
        if int(item.get("end_ms", 0)) > int(item.get("start_ms", 0))
    ]
    levels: dict[int, float] = {}
    for segment in manifest.get("segments") or []:
        idx = int(segment["idx"])
        if idx not in segment_indices:
            continue
        start_ms = int(segment["start_ms"])
        end_ms = int(segment["end_ms"])
        voiced_ranges = [
            (max(start_ms, start), min(end_ms, end))
            for start, end in speech_ranges
            if end > start_ms and start < end_ms
        ]
        if not voiced_ranges:
            voiced_ranges = [(start_ms, end_ms)]
        weighted_power = 0.0
        total_duration = 0
        for range_start, range_end in voiced_ranges:
            duration = max(1, range_end - range_start)
            level_db = _mean_volume_db(vocals, range_start, range_end)
            weighted_power += (10 ** (level_db / 10)) * duration
            total_duration += duration
        levels[idx] = round(
            10 * math.log10(max(weighted_power / max(1, total_duration), 1e-12)),
            2,
        )
    return levels


def _matched_loudness_gain(source_level_db: float, tts_level_db: float) -> float:
    """Match generated speech to its source slot while avoiding clipping/noise."""
    return round(max(-8.0, min(6.0, source_level_db - tts_level_db)), 2)


def _generate_dub_voice(request: DubVoiceRequest) -> dict:
    work_dir = DATA_ROOT / request.run_id
    if not work_dir.is_dir():
        raise RuntimeError("해당 추출 작업을 찾을 수 없습니다.")
    manifest = json.loads((work_dir / "manifest.json").read_text(encoding="utf-8"))
    source_segments = {
        int(segment["idx"]): segment
        for segment in manifest.get("segments") or []
    }
    speaker_by_idx = {
        segment.idx: str(
            source_segments.get(segment.idx, {}).get("speaker_id") or "speaker_0"
        )
        for segment in request.segments
    }
    voices: dict[str, tuple[str, bool]] = {}
    for speaker_id in sorted(set(speaker_by_idx.values())):
        voices[speaker_id] = _create_eleven_voice(work_dir, speaker_id)
    source_levels = _source_loudness_levels(
        work_dir,
        {segment.idx for segment in request.segments},
    )
    base = os.getenv("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io").rstrip("/")
    target_language = str(manifest.get("target_language") or "")
    model = tts_model_for_language(
        os.getenv("ELEVENLABS_TTS_MODEL", "eleven_multilingual_v2"),
        target_language,
    )
    settings = {
        "neutral": {"stability": 0.55, "similarity_boost": 0.75, "style": 0.0},
        "warm": {"stability": 0.48, "similarity_boost": 0.78, "style": 0.25},
        "energetic": {"stability": 0.32, "similarity_boost": 0.72, "style": 0.65},
        "serious": {"stability": 0.75, "similarity_boost": 0.8, "style": 0.15},
    }[request.tone_style]
    output_dir = work_dir / "dubbed_speech"
    output_dir.mkdir(exist_ok=True)
    outputs: list[dict[str, object]] = []
    try:
        for position, segment in enumerate(request.segments):
            filename = f"{segment.idx + 1:04d}.mp3"
            speaker_id = speaker_by_idx[segment.idx]
            voice_id = voices[speaker_id][0]
            tts_body: dict[str, object] = {
                "text": segment.target_text,
                "model_id": model,
                "voice_settings": {**settings, "use_speaker_boost": True},
                "apply_text_normalization": "on",
            }
            if target_language and model != "eleven_multilingual_v2":
                tts_body["language_code"] = target_language.lower().split("-", 1)[0]
            if position > 0:
                tts_body["previous_text"] = request.segments[
                    position - 1
                ].target_text
            if position + 1 < len(request.segments):
                tts_body["next_text"] = request.segments[
                    position + 1
                ].target_text
            response = httpx.post(
                f"{base}/v1/text-to-speech/{voice_id}",
                params={"output_format": "mp3_44100_128"},
                headers={**_eleven_headers(), "Content-Type": "application/json"},
                json=tts_body,
                timeout=300,
            )
            if response.status_code >= 400:
                raise RuntimeError(
                    f"ElevenLabs TTS 실패 ({response.status_code}): "
                    f"{response.text[:300]}"
                )
            output_path = output_dir / filename
            output_path.write_bytes(response.content)
            tts_level = _mean_volume_db(
                output_path,
                0,
                max(1, int(_audio_duration(output_path) * 1000)),
            )
            source_level = source_levels.get(segment.idx, tts_level)
            gain_db = _matched_loudness_gain(source_level, tts_level)
            outputs.append(
                {
                    "idx": segment.idx,
                    "speaker_id": speaker_id,
                    "source_level_db": source_level,
                    "tts_level_db": tts_level,
                    "gain_db": gain_db,
                    "audio_url": (
                        f"/v1/local/step12/{request.run_id}/dubbed_speech/{filename}"
                    ),
                }
            )
    finally:
        for voice_id, temporary in voices.values():
            if not temporary:
                continue
            try:
                httpx.delete(
                    f"{base}/v1/voices/{voice_id}",
                    headers=_eleven_headers(),
                    timeout=30,
                )
            except httpx.HTTPError:
                pass
    (work_dir / "dub_voice_manifest.json").write_text(
        json.dumps(
            {
                "segments": [
                    {
                        key: value
                        for key, value in output.items()
                        if key != "audio_url"
                    }
                    for output in outputs
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {"run_id": request.run_id, "segments": outputs}


def _source_file(work_dir: Path) -> Path:
    sources = sorted(work_dir.glob("source.*"))
    if not sources:
        raise RuntimeError("원본 영상 파일을 찾을 수 없습니다.")
    return sources[0]


def _separate_no_vocals(work_dir: Path) -> tuple[Path, Path]:
    audio = work_dir / "original_audio.wav"
    if not audio.is_file():
        raise RuntimeError("먼저 오디오·자막 추출을 실행해 주세요.")
    model = os.getenv("DEMUCS_MODEL", "htdemucs_ft")
    stem_root = work_dir / "stems"
    stem_dir = stem_root / model / audio.stem
    vocals = stem_dir / "vocals.wav"
    no_vocals = stem_dir / "no_vocals.wav"
    if vocals.is_file() and no_vocals.is_file():
        return vocals, no_vocals
    _run_command(
        [
            sys.executable,
            "-m",
            "demucs.separate",
            "-n",
            model,
            "--two-stems",
            "vocals",
            "-d",
            os.getenv("DEMUCS_DEVICE", "cpu"),
            "-j",
            os.getenv("DEMUCS_JOBS", "1"),
            "-o",
            str(stem_root),
            str(audio),
        ],
        "Demucs 보이스 분리",
    )
    if not vocals.is_file() or not no_vocals.is_file():
        raise RuntimeError(f"Demucs 결과를 찾을 수 없습니다: {stem_dir}")
    return vocals, no_vocals


def _build_selective_speech_removed_bed(
    original: Path,
    no_vocals: Path,
    ranges_ms: list[tuple[int, int]],
    output: Path,
) -> None:
    """Remove vocals only while ASR-recognized language is present.

    Outside those timestamps the original waveform is passed through
    unchanged, preserving cheers, crying, singing, music, and ambience.
    """
    mask = _speech_mask_expression(ranges_ms)
    filters = (
        f"[0:a]aresample=44100,volume=eval=frame:volume='1-({mask})'[original];"
        f"[1:a]aresample=44100,volume=eval=frame:volume='{mask}'[removed];"
        "[original][removed]amix=inputs=2:duration=first:normalize=0[bed]"
    )
    _run_ffmpeg(
        [
            "-i",
            str(original),
            "-i",
            str(no_vocals),
            "-filter_complex",
            filters,
            "-map",
            "[bed]",
            "-c:a",
            "pcm_s16le",
            "-ar",
            "44100",
            "-ac",
            "2",
            str(output),
        ]
    )


def _fit_dub_clip(
    source: Path,
    output: Path,
    slot_seconds: float,
    gain_db: float = 0.0,
) -> tuple[float, bool]:
    duration = _audio_duration(source)
    if duration <= 0 or slot_seconds <= 0:
        raise RuntimeError(f"유효하지 않은 더빙 구간입니다: {source.name}")
    # Fit speech exactly inside its non-overlapping timestamp slot. We slow
    # down only to 0.85x; shorter clips retain their natural pace and are
    # padded with silence.
    requested = duration / slot_seconds
    tempo = min(max(requested, 0.85), 2.0)
    audible = min(slot_seconds, duration / tempo)
    fade = min(0.2, audible / 2)
    filters = _atempo_filters(tempo)
    filters.extend(
        [
            f"volume={gain_db:.2f}dB",
            f"afade=t=in:st=0:d={fade:.3f}",
            f"afade=t=out:st={max(0, audible - fade):.3f}:d={fade:.3f}",
            f"apad=pad_dur={slot_seconds:.3f}",
            f"atrim=duration={slot_seconds:.3f}",
            "asetpts=PTS-STARTPTS",
        ]
    )
    _run_ffmpeg(
        [
            "-i",
            str(source),
            "-af",
            ",".join(filters),
            "-c:a",
            "pcm_s16le",
            "-ar",
            "44100",
            "-ac",
            "2",
            str(output),
        ]
    )
    return tempo, requested > 2.0


def _mix_dubbed_audio(
    no_vocals: Path,
    clips: list[tuple[Path, int]],
    output: Path,
) -> None:
    args = ["-i", str(no_vocals)]
    for clip, _ in clips:
        args.extend(["-i", str(clip)])
    filters: list[str] = ["[0:a]aresample=44100[bed]"]
    labels = ["[bed]"]
    for input_idx, (_, start_ms) in enumerate(clips, start=1):
        filters.append(
            f"[{input_idx}:a]adelay={max(0, start_ms)}:all=1[d{input_idx}]"
        )
        labels.append(f"[d{input_idx}]")
    filters.append(
        "".join(labels)
        + f"amix=inputs={len(labels)}:duration=first:normalize=0,"
        + "alimiter=limit=0.98[mix]"
    )
    _run_ffmpeg(
        [
            *args,
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[mix]",
            "-c:a",
            "pcm_s16le",
            "-ar",
            "44100",
            "-ac",
            "2",
            str(output),
        ]
    )


def _mux_video(
    source: Path,
    audio: Path,
    output: Path,
    ass_path: Path | None = None,
) -> None:
    args = [
        "-i",
        str(source),
        "-i",
        str(audio),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
    ]
    if ass_path is None:
        args.extend(["-c:v", "copy"])
    else:
        escaped = (
            str(ass_path)
            .replace("\\", "/")
            .replace(":", "\\:")
            .replace("'", "\\'")
        )
        args.extend(
            [
                "-vf",
                f"ass='{escaped}'",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
            ]
        )
    args.extend(
        [
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            "-shortest",
            str(output),
        ]
    )
    _run_ffmpeg(args)


def _render_dubbed_video(request: RenderDubRequest) -> dict:
    work_dir = DATA_ROOT / request.run_id
    if not work_dir.is_dir():
        raise RuntimeError("해당 추출 작업을 찾을 수 없습니다.")
    source = _source_file(work_dir)
    _, no_vocals = _separate_no_vocals(work_dir)
    ordered = sorted(request.segments, key=lambda segment: (segment.start_ms, segment.idx))
    manifest_path = work_dir / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.is_file()
        else {}
    )
    saved_ranges = manifest.get("speech_ranges") or []
    speech_ranges = [
        (int(item["start_ms"]), int(item["end_ms"]))
        for item in saved_ranges
        if int(item.get("end_ms", 0)) > int(item.get("start_ms", 0))
    ]
    if not speech_ranges:
        # Compatibility fallback for runs extracted before word-level masks.
        speech_ranges = [
            (segment.start_ms, segment.end_ms)
            for segment in ordered
            if segment.source_text.strip() and segment.end_ms > segment.start_ms
        ]
    else:
        speech_ranges = _cover_recognized_phrase_boundaries(
            speech_ranges,
            [
                (segment.start_ms, segment.end_ms)
                for segment in ordered
                if segment.source_text.strip() and segment.end_ms > segment.start_ms
            ],
        )
    if not speech_ranges:
        raise RuntimeError("언어로 인식된 음성 타임스탬프가 없습니다.")

    # Use the untouched source audio outside ASR speech ranges. Inside each
    # range, transition to Demucs no_vocals with a 0.2-second crossfade.
    selective_bed = work_dir / "speech_removed.wav"
    _build_selective_speech_removed_bed(
        work_dir / "original_audio.wav",
        no_vocals,
        speech_ranges,
        selective_bed,
    )

    # Keep the intermediate selectively voice-removed video for inspection.
    voice_removed = work_dir / "voice_removed.mp4"
    _mux_video(source, selective_bed, voice_removed)

    fitted_dir = work_dir / "fitted_dub"
    fitted_dir.mkdir(exist_ok=True)
    placed: list[tuple[Path, int]] = []
    warnings: list[str] = []
    dub_manifest_path = work_dir / "dub_voice_manifest.json"
    dub_manifest = (
        json.loads(dub_manifest_path.read_text(encoding="utf-8"))
        if dub_manifest_path.is_file()
        else {}
    )
    gain_by_idx = {
        int(item["idx"]): float(item.get("gain_db", 0.0))
        for item in dub_manifest.get("segments") or []
    }
    for position, segment in enumerate(ordered):
        raw = work_dir / "dubbed_speech" / f"{segment.idx + 1:04d}.mp3"
        if not raw.is_file() or not segment.target_text.strip():
            continue
        next_start = (
            ordered[position + 1].start_ms
            if position + 1 < len(ordered)
            else segment.end_ms
        )
        safe_end = min(segment.end_ms, next_start)
        slot = max(0.001, (safe_end - segment.start_ms) / 1000)
        fitted = fitted_dir / f"{segment.idx + 1:04d}.wav"
        tempo, truncated = _fit_dub_clip(
            raw,
            fitted,
            slot,
            gain_by_idx.get(segment.idx, 0.0),
        )
        if truncated:
            warnings.append(
                f"segment_{segment.idx}: 2배속으로도 길어 구간 끝에서 잘렸습니다."
            )
        elif tempo > 1.15:
            warnings.append(
                f"segment_{segment.idx}: 타임스탬프에 맞춰 {tempo:.2f}배속 처리했습니다."
            )
        placed.append((fitted, segment.start_ms))
    if not placed:
        raise RuntimeError("합성할 ElevenLabs 더빙 음성이 없습니다.")

    mixed = work_dir / "dubbed_mix.wav"
    _mix_dubbed_audio(selective_bed, placed, mixed)

    ass_path: Path | None = None
    if request.subtitle_mode != "none":
        from .worker.subtitles import build_ass

        rows = [
            {
                "start_ms": segment.start_ms,
                "end_ms": segment.end_ms,
                "source_text": segment.source_text,
                "target_text": segment.target_text,
            }
            for segment in ordered
        ]
        ass_text = build_ass(rows, request.subtitle_mode)  # type: ignore[arg-type]
        if ass_text:
            ass_path = work_dir / "subtitles.ass"
            ass_path.write_text(ass_text, encoding="utf-8")

    output = work_dir / "dubbed_output.mp4"
    _mux_video(source, mixed, output, ass_path)
    return {
        "run_id": request.run_id,
        "voice_removed_url": (
            f"/v1/local/step12/{request.run_id}/voice_removed.mp4"
        ),
        "output_url": f"/v1/local/step12/{request.run_id}/dubbed_output.mp4",
        "warnings": warnings,
    }


app = FastAPI(title="Dubby local step 1-2 verifier")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Filename"],
)


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "ok": True,
        "mode": "local-step12",
        "model": os.getenv("LOCAL_WHISPER_MODEL", "medium"),
        "openai_configured": bool(os.getenv("OPENAI_API_KEY", "").strip()),
        "elevenlabs_configured": bool(os.getenv("ELEVENLABS_API_KEY", "").strip()),
    }


@app.post("/v1/local/step12")
async def create_step12(
    request: Request,
    source_lang: Annotated[str, Query(pattern="^(ko|en|vi)$")] = "ko",
    target_lang: Annotated[str, Query(pattern="^(ko|en|vi)$")] = "en",
    diarization_enabled: Annotated[bool, Query()] = False,
    x_filename: Annotated[str, Header()] = "source.mp4",
) -> dict:
    if source_lang not in SUPPORTED_LANGUAGES:
        raise HTTPException(400, "지원하지 않는 원어입니다.")
    run_id = uuid4().hex
    work_dir = DATA_ROOT / run_id
    work_dir.mkdir(parents=True, exist_ok=False)
    suffix = Path(x_filename).suffix.lower() or ".bin"
    source = work_dir / f"source{suffix}"

    size = 0
    try:
        with source.open("wb") as output:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_SOURCE_BYTES:
                    raise HTTPException(413, "파일은 최대 500MB까지 지원합니다.")
                output.write(chunk)
        if size == 0:
            raise HTTPException(400, "빈 파일입니다.")
        return await asyncio.to_thread(
            _process,
            source,
            work_dir,
            source_lang,
            target_lang,
            diarization_enabled,
        )
    except HTTPException:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
    except Exception as exc:
        # Keep successful intermediate audio when ASR fails so step 1 can still
        # be inspected; the response points developers to the run directory.
        raise HTTPException(
            500,
            {
                "message": str(exc),
                "run_id": run_id,
                "work_dir": str(work_dir),
            },
        ) from exc


@app.post("/v1/local/dub-voice")
async def create_dub_voice(body: DubVoiceRequest) -> dict:
    try:
        return await asyncio.to_thread(_generate_dub_voice, body)
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@app.post("/v1/local/render-dub")
async def render_dub(body: RenderDubRequest) -> dict:
    try:
        return await asyncio.to_thread(_render_dubbed_video, body)
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@app.get("/v1/local/step12/{run_id}/{asset_path:path}")
async def get_asset(
    run_id: str,
    asset_path: str,
    download: Annotated[str | None, Query(max_length=200)] = None,
) -> FileResponse:
    root = (DATA_ROOT / run_id).resolve()
    candidate = (root / asset_path).resolve()
    if root not in candidate.parents or not candidate.is_file():
        raise HTTPException(404, "결과 파일을 찾을 수 없습니다.")
    if download is not None:
        # Cross-origin <a download> is ignored by browsers, so the local
        # server must send Content-Disposition: attachment itself.
        filename = Path(download).name or candidate.name
        return FileResponse(
            candidate,
            filename=filename,
            content_disposition_type="attachment",
        )
    return FileResponse(candidate)
