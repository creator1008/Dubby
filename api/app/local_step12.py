"""Local-only verification server for pipeline steps 1 and 2.

Run from ``api``:

    uvicorn app.local_step12:app --reload --port 8002

Extracts audio, transcribes speech, and runs dubbing. Bulky media stays in the
local scratch directory between steps by default (set LOCAL_PURGE_SCRATCH=1 to
purge after R2 upload). R2 remains a backup / cross-process hydrate source.
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
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from dotenv import load_dotenv
import httpx

from .local_r2_store import LocalR2Store
from .remote_media import RemoteMediaError, ingest_remote_media
from .worker.dub_quality import (
    cover_recognized_phrase_boundaries as _cover_recognized_phrase_boundaries,
    matched_loudness_gain as _matched_loudness_gain,
    source_loudness_levels as _shared_source_loudness_levels,
    voice_removal_ranges as _voice_removal_ranges,
)
from .languages import LANGUAGE_ALIASES, LANG_QUERY_PATTERN, LANGUAGE_NAMES, SUPPORTED_LANGUAGES
from .worker.elevenlabs_client import tts_model_for_language
from .worker.media import merge_speech_ranges as _merge_speech_ranges
from .worker.timing import (
    ELEVENLABS_SPEAK_SPEED_MAX,
    ELEVENLABS_SPEAK_SPEED_MIN,
    initial_speak_speed,
    speak_speed_for_slot,
    tempo_filters,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
# The user-facing local setup stores provider keys in the repository root.
# api/.env remains supported and takes precedence when both exist.
load_dotenv(REPO_ROOT / ".env")
load_dotenv(REPO_ROOT / "api" / ".env", override=True)

MAX_SOURCE_BYTES = 500 * 1024 * 1024
SCRATCH_ROOT = REPO_ROOT / ".local-data" / "scratch"
DEMO_STATE_ROOT = REPO_ROOT / ".local-data" / "demo-state"
INITIAL_PROMPTS = {
    code: (
        f"This is an accurate {LANGUAGE_NAMES.get(code, code)} transcript "
        "with correct names and punctuation."
    )
    for code in SUPPORTED_LANGUAGES
}
INITIAL_PROMPTS.update(
    {
        "ko": "정확한 한국어 받아쓰기입니다. 고유명사, 띄어쓰기, 문장 부호를 정확히 표기합니다.",
        "en": "This is an accurate English transcript with correct names and punctuation.",
        "vi": "Đây là bản chép lời tiếng Việt chính xác với tên riêng và dấu câu.",
        "zh": "这是准确的普通话转写，请正确标注专有名词和标点。",
        "ja": "これは正確な日本語の書き起こしです。固有名詞と句読点を正しく記してください。",
        "es": "Esta es una transcripción precisa en español con nombres propios y puntuación correctos.",
        "fr": "Ceci est une transcription française précise avec noms propres et ponctuation corrects.",
        "pt": "Esta é uma transcrição precisa em português com nomes próprios e pontuação corretos.",
        "de": "Dies ist eine genaue deutsche Transkription mit korrekten Namen und Interpunktion.",
        "ru": "Это точная русская транскрипция с правильными именами и пунктуацией.",
        "ar": "هذا تفريغ دقيق باللغة العربية مع الأسماء وعلامات الترقيم الصحيحة.",
        "ur": "یہ درست اردو نقلِ کلام ہے؛ مناسب اسمائے خاص اور رموزِ اوقاف لکھیں۔",
        "id": "Ini adalah transkrip Bahasa Indonesia yang akurat dengan nama dan tanda baca yang benar.",
        "ms": "Ini adalah transkrip Bahasa Melayu yang tepat dengan nama dan tanda baca yang betul.",
        "tr": "Bu, özel adlar ve noktalama işaretleri doğru yazılmış doğru bir Türkçe dökümdür.",
        "ta": "இது சரியான தமிழ் எழுத்துப்படி; சரியான பெயர்களும் நிறுத்தற்குறிகளும் இருக்கட்டும்.",
    }
)
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
    speak_speed: float | None = Field(default=None, ge=0.5, le=1.5)
    emotion_tone: str | None = Field(
        default=None,
        pattern="^(sad|angry|whisper|excited|energetic|calm|cheerful)$",
    )


class DubVoiceRequest(BaseModel):
    run_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    segments: list[DubSegment] = Field(min_length=1, max_length=500)
    tone_style: str = Field(
        default="calm",
        pattern=(
            "^(sad|angry|whisper|excited|energetic|calm|cheerful|"
            "neutral|warm|serious)$"
        ),
    )
    voice_ids: list[str] = Field(default_factory=list, max_length=8)

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


class FromUrlRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


class RetranslateSegment(BaseModel):
    idx: int
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    source_text: str = Field(default="", max_length=2000)


class RetranslateRequest(BaseModel):
    source_lang: str = Field(min_length=2, max_length=16)
    target_lang: str = Field(min_length=2, max_length=16)
    segments: list[RetranslateSegment] = Field(min_length=1, max_length=500)


class GcRunsRequest(BaseModel):
    """Keep only these run_ids; delete every other local/R2 run media."""

    keep_run_ids: list[str] = Field(default_factory=list, max_length=500)


_RUN_ID_RE = re.compile(r"^[a-f0-9]{32}$")


def _scratch_dir(run_id: str) -> Path:
    return SCRATCH_ROOT / run_id


def _assert_run_id(run_id: str) -> str:
    value = (run_id or "").strip().lower()
    if not _RUN_ID_RE.fullmatch(value):
        raise HTTPException(400, "유효하지 않은 run_id입니다.")
    return value


def _delete_local_scratch(run_id: str) -> bool:
    work_dir = _scratch_dir(run_id)
    if not work_dir.is_dir():
        return False
    shutil.rmtree(work_dir, ignore_errors=True)
    return not work_dir.exists()


def _delete_run_storage(run_id: str) -> dict[str, object]:
    """Permanently remove one run from local scratch and R2. No recovery."""
    local_removed = _delete_local_scratch(run_id)
    r2_deleted = 0
    r2_error: str | None = None
    try:
        r2_deleted = LocalR2Store().delete_run(run_id)
    except Exception as exc:  # noqa: BLE001 - best-effort storage cleanup
        r2_error = str(exc)
    return {
        "run_id": run_id,
        "local_removed": local_removed,
        "r2_objects_deleted": r2_deleted,
        "r2_error": r2_error,
    }


def _list_known_run_ids() -> set[str]:
    found: set[str] = set()
    if SCRATCH_ROOT.is_dir():
        for path in SCRATCH_ROOT.iterdir():
            if path.is_dir() and _RUN_ID_RE.fullmatch(path.name):
                found.add(path.name)
    try:
        found |= {
            run_id
            for run_id in LocalR2Store().list_run_ids()
            if _RUN_ID_RE.fullmatch(run_id)
        }
    except Exception:
        pass
    return found


def _gc_orphan_runs(keep_run_ids: set[str]) -> dict[str, object]:
    keep = {run_id for run_id in keep_run_ids if _RUN_ID_RE.fullmatch(run_id)}
    deleted: list[dict[str, object]] = []
    for run_id in sorted(_list_known_run_ids() - keep):
        deleted.append(_delete_run_storage(run_id))
    return {
        "kept": sorted(keep),
        "deleted_count": len(deleted),
        "deleted": deleted,
    }


def _should_purge_scratch() -> bool:
    """When true, delete local media after R2 upload (legacy / disk-tight mode)."""
    return os.getenv("LOCAL_PURGE_SCRATCH", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _audio_upload_content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        return "audio/mpeg"
    if suffix in {".wav", ".wave"}:
        return "audio/wav"
    return "application/octet-stream"


_demucs_locks: dict[str, threading.Lock] = {}
_bg_lock = threading.Lock()
_bg_tasks: set[threading.Thread] = set()


def _spawn_background(name: str, target) -> None:
    def _runner() -> None:
        try:
            target()
        except Exception:
            pass
        finally:
            with _bg_lock:
                _bg_tasks.discard(thread)

    thread = threading.Thread(target=_runner, name=name, daemon=True)
    with _bg_lock:
        _bg_tasks.add(thread)
    thread.start()


def _resolve_work_dir(run_id: str) -> Path:
    """Return a scratch dir, hydrating from R2 only when local media is missing."""
    work_dir = _scratch_dir(run_id)
    work_dir.mkdir(parents=True, exist_ok=True)
    needs_hydrate = not (work_dir / "manifest.json").is_file()
    if not needs_hydrate:
        try:
            _source_file(work_dir)
        except RuntimeError:
            needs_hydrate = True
    if not needs_hydrate and not (work_dir / "original_audio.wav").is_file():
        needs_hydrate = True
    if needs_hydrate:
        LocalR2Store().sync_run_from_r2(run_id, work_dir)
    if not (work_dir / "manifest.json").is_file():
        raise RuntimeError("해당 추출 작업을 찾을 수 없습니다.")
    if not (work_dir / "original_audio.wav").is_file():
        raise RuntimeError(
            "더빙 재생성에 필요한 원본 오디오가 없습니다. "
            "최종 영상만 보관된 이전 작업은 파일을 다시 추출해 주세요."
        )
    return work_dir


def _manifest_with_proxy_urls(manifest: dict, run_id: str, work_dir: Path) -> dict:
    published = dict(manifest)
    published["storage"] = "r2"
    source_rel = _source_file(work_dir).relative_to(work_dir).as_posix()
    published["source_url"] = _local_asset_url(run_id, source_rel)
    published["audio_url"] = _local_asset_url(run_id, published["audio_path"])
    published["asr_audio_url"] = _local_asset_url(run_id, published["asr_audio_path"])
    segments = []
    for segment in published.get("segments") or []:
        item = dict(segment)
        item["audio_url"] = _local_asset_url(run_id, item["audio_path"])
        segments.append(item)
    published["segments"] = segments
    return published


def _sync_run_to_r2_safe(run_id: str, work_dir: Path) -> None:
    store = LocalR2Store()
    store.sync_run_to_r2(run_id, work_dir)
    for name in ("manifest.json", "dub_voice_manifest.json"):
        path = work_dir / name
        if path.is_file():
            store.upload_file(run_id, path, name)
    if _should_purge_scratch():
        store.purge_work_dir_media(work_dir)


def _publish_run_to_r2(work_dir: Path, manifest: dict) -> dict:
    """Write proxy URLs locally and schedule R2 backup + Demucs pre-warm."""
    run_id = manifest["run_id"]
    published = _manifest_with_proxy_urls(manifest, run_id, work_dir)
    (work_dir / "manifest.json").write_text(
        json.dumps(published, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _schedule_post_extract(run_id, work_dir)
    return published


def _local_asset_url(run_id: str, relative: str) -> str:
    return f"/v1/local/step12/{run_id}/{relative.lstrip('/')}"


def _finalize_run_upload(run_id: str, work_dir: Path) -> None:
    """Keep local media for the next step; backup to R2 asynchronously."""
    _spawn_background(
        f"r2-finalize-{run_id[:8]}",
        lambda: _sync_run_to_r2_safe(run_id, work_dir),
    )


def _finalize_final_outputs(run_id: str, work_dir: Path, source: Path) -> None:
    """Keep local stems/audio for re-dub; backup final outputs to R2 async."""
    del source  # kept for call-site compatibility
    output = work_dir / "dubbed_output.mp4"
    if not output.is_file():
        raise RuntimeError("최종 더빙 영상을 찾을 수 없습니다.")
    _spawn_background(
        f"r2-final-{run_id[:8]}",
        lambda: _sync_run_to_r2_safe(run_id, work_dir),
    )


def _prewarm_stems(run_id: str) -> None:
    """Run Demucs during the edit window so dubbing starts with stems ready."""
    work_dir = _resolve_work_dir(run_id)
    _separate_no_vocals(work_dir)


def _schedule_post_extract(run_id: str, work_dir: Path) -> None:
    """Backup extract artifacts and pre-warm Demucs while the user edits."""

    def _run() -> None:
        keep_local = not _should_purge_scratch()
        with ThreadPoolExecutor(max_workers=2) as pool:
            sync_future = pool.submit(_sync_run_to_r2_safe, run_id, work_dir)
            demucs_future = (
                pool.submit(_prewarm_stems, run_id) if keep_local else None
            )
            try:
                sync_future.result()
            except Exception:
                pass
            if demucs_future is not None:
                try:
                    demucs_future.result()
                except Exception:
                    pass
                try:
                    LocalR2Store().sync_run_to_r2(run_id, work_dir)
                except Exception:
                    pass

    _spawn_background(f"post-extract-{run_id[:8]}", _run)


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
    return tempo_filters(factor, rubberband_available=False)


@lru_cache(maxsize=1)
def _ffmpeg_has_rubberband() -> bool:
    result = subprocess.run(
        [_ffmpeg_executable(), "-hide_banner", "-filters"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    blob = f"{result.stdout}\n{result.stderr}"
    return bool(re.search(r"\brubberband\b", blob))


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


def _whisper_segment_is_hallucination(segment: dict) -> bool:
    """Drop Whisper segments that look like music/noise hallucinations."""
    text = str(segment.get("text", "")).strip()
    if not text:
        return True
    start = float(segment.get("start", 0.0) or 0.0)
    end = float(segment.get("end", 0.0) or 0.0)
    if end <= start:
        return True
    no_speech = float(segment.get("no_speech_prob", 0.0) or 0.0)
    avg_logprob = float(segment.get("avg_logprob", 0.0) or 0.0)
    compression = float(segment.get("compression_ratio", 0.0) or 0.0)
    # OpenAI/Whisper guidance: high compression usually means looping junk.
    if compression >= 2.4:
        return True
    if no_speech > 0.6 and avg_logprob < -0.8:
        return True
    if no_speech > 0.85:
        return True
    # Tiny fragments near the end of music clips are almost always garbage.
    if end - start < 0.35 and no_speech > 0.4:
        return True
    # Detect immediate phrase loops inside one segment ("A A A").
    tokens = re.findall(r"\S+", text)
    if len(tokens) >= 6:
        for n in (2, 3, 4):
            if len(tokens) < n * 3:
                continue
            ngrams = [
                " ".join(tokens[i : i + n]) for i in range(0, len(tokens) - n + 1, n)
            ]
            if len(ngrams) >= 3 and len(set(ngrams)) == 1:
                return True
    return False


def _dedupe_repetitive_drafts(
    drafts: list[tuple[int, int, str]],
) -> list[tuple[int, int, str]]:
    """Collapse consecutive identical/near-identical hallucinated lines."""
    cleaned: list[tuple[int, int, str]] = []
    for start, end, text in drafts:
        compact = re.sub(r"\s+", "", text)
        if cleaned:
            prev_start, prev_end, prev_text = cleaned[-1]
            prev_compact = re.sub(r"\s+", "", prev_text)
            if compact == prev_compact or (
                compact and prev_compact and compact in prev_compact and len(compact) >= 6
            ):
                cleaned[-1] = (prev_start, max(prev_end, end), prev_text)
                continue
            if prev_compact and compact and prev_compact in compact and len(prev_compact) >= 6:
                cleaned[-1] = (prev_start, max(prev_end, end), text)
                continue
        cleaned.append((start, end, text))
    return cleaned


def _drafts_look_hallucinated(drafts: list[tuple[int, int, str]]) -> bool:
    """True when the transcript is dominated by a looping junk phrase."""
    if not drafts:
        return False
    tokens = re.findall(r"\S+", " ".join(text for _, _, text in drafts))
    if len(tokens) < 8:
        return False
    counts = Counter(tokens)
    top_count = counts.most_common(1)[0][1]
    if top_count / len(tokens) >= 0.3:
        return True
    # Many short drafts that share the same first few characters.
    if len(drafts) >= 4:
        heads = [re.sub(r"\s+", "", text)[:8] for _, _, text in drafts if text.strip()]
        if heads and max(Counter(heads).values()) / len(heads) >= 0.5:
            return True
    return False


def _openai_transcribe(
    asr_wav: Path,
    language: str | None,
) -> tuple[list[tuple[int, int, str]], list[tuple[int, int]], str | None]:
    """Use OpenAI Whisper word timestamps when an API key is configured.

    Returns ``(drafts, word_ranges, detected_language)``. When ``language`` is
    None, Whisper auto-detects and the detected code is returned.
    """
    base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("WHISPER_MODEL", "whisper-1")
    # No initial prompt here: whisper-1 can echo the prompt verbatim into the
    # transcript on noisy inputs, replacing the actual speech.
    form: dict[str, object] = {
        "model": model,
        "response_format": "verbose_json",
        "timestamp_granularities[]": ["word", "segment"],
        "temperature": 0,
    }
    if language:
        form["language"] = language
    with asr_wav.open("rb") as audio:
        response = httpx.post(
            f"{base}/audio/transcriptions",
            headers=_openai_headers(),
            data=form,
            files={
                "file": (
                    asr_wav.name,
                    audio,
                    _audio_upload_content_type(asr_wav),
                )
            },
            timeout=600,
        )
    if response.status_code >= 400:
        raise RuntimeError(
            f"OpenAI 음성인식 실패 ({response.status_code}): {response.text[:300]}"
        )
    payload = response.json()
    detected = str(payload.get("language") or "").strip().lower() or None
    # Normalize verbose_json language names to our ISO codes when needed.
    if detected:
        detected = LANGUAGE_ALIASES.get(detected, detected)
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
    raw_segments = payload.get("segments") or []
    kept_segments = 0
    for segment in raw_segments:
        if _whisper_segment_is_hallucination(segment):
            continue
        kept_segments += 1
        start = max(0, round(float(segment.get("start", 0)) * 1000))
        end = round(float(segment.get("end", 0)) * 1000)
        text = str(segment.get("text", "")).strip()
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

    if not drafts and not raw_segments:
        # No segment metadata at all — fall back to word grouping.
        usable_words = [
            word
            for word in words
            if word.text.strip() and word.end_ms - word.start_ms >= 40
        ]
        if usable_words and len(usable_words) <= 80:
            drafts = group_words(usable_words, gap_ms=500, max_duration_ms=4500)
    elif not drafts and raw_segments and kept_segments == 0:
        # Every segment looked like music/noise hallucination. Do not promote
        # the accompanying word list (it is usually the same junk loop); the
        # caller can retry with auto-detect or another language.
        drafts = []

    drafts = _dedupe_repetitive_drafts(drafts)
    if _drafts_look_hallucinated(drafts):
        drafts = []

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
    return (
        non_overlapping,
        word_ranges or [(start, end) for start, end, _ in non_overlapping],
        detected,
    )


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
            files={
                "file": (
                    asr_wav.name,
                    audio,
                    _audio_upload_content_type(asr_wav),
                )
            },
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
    """Precisely align linguistic words while excluding sobbing and pauses.

    Disabled by default when OpenAI ASR is used: loading faster-whisper
    ``medium`` on CPU often stalls TikTok/short-form uploads for many minutes.
    Set ``LOCAL_SPEECH_ALIGN=1`` to opt in.
    """
    flag = os.getenv("LOCAL_SPEECH_ALIGN", "0").strip().lower()
    if flag not in {"1", "true", "yes", "on"}:
        return []
    try:
        model = _whisper_model()
    except RuntimeError:
        return []
    try:
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
    except Exception:
        # Alignment is best-effort; never block the upload on local model issues.
        return []


def _transcribe(
    asr_wav: Path,
    language: str,
) -> tuple[list[tuple[int, int, str]], list[tuple[int, int]], str]:
    """Return drafts, speech ranges, and the language code used for the text."""
    if os.getenv("OPENAI_API_KEY", "").strip():
        drafts, api_ranges, _detected = _openai_transcribe(asr_wav, language)
        if drafts:
            aligned_ranges = _local_speech_ranges(asr_wav, language)
            return drafts, aligned_ranges or api_ranges, language

        # Wrong source language (common on TikTok) yields music-like Korean
        # hallucinations. Retry with auto-detect, then with the detected code.
        drafts, api_ranges, detected = _openai_transcribe(asr_wav, None)
        if drafts:
            use_lang = detected if detected in SUPPORTED_LANGUAGES else language
            aligned_ranges = _local_speech_ranges(asr_wav, use_lang)
            return drafts, aligned_ranges or api_ranges, use_lang
        if detected and detected in SUPPORTED_LANGUAGES and detected != language:
            drafts, api_ranges, _ = _openai_transcribe(asr_wav, detected)
            if drafts:
                aligned_ranges = _local_speech_ranges(asr_wav, detected)
                return drafts, aligned_ranges or api_ranges, detected
        return [], api_ranges, language

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
    return drafts, word_ranges or [(start, end) for start, end, _ in drafts], language


def _extract_json_blob(content: str) -> object:
    """Parse JSON content, tolerating fences and leading/trailing prose."""
    raw = (content or "").strip()
    if not raw:
        raise ValueError("empty translation content")
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", raw)
        if not match:
            raise
        return json.loads(match.group(1))


def _translation_item_text(item: object) -> str | None:
    if isinstance(item, str):
        return item.strip()
    if not isinstance(item, dict):
        return None
    text_raw = item.get("text", item.get("translation", item.get("target_text")))
    if text_raw is None:
        return None
    return str(text_raw).strip()


def _parse_translation_payload(
    content: str,
    expected_idxs: list[int],
) -> dict[int, str]:
    """Accept common OpenAI JSON shapes and recover missing idxs when possible."""
    data = _extract_json_blob(content)
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = (
            data.get("translations")
            or data.get("translation")
            or data.get("segments")
            or data.get("items")
            or data.get("results")
        )
        if items is None and data and all(str(k).isdigit() for k in data.keys()):
            items = [{"idx": int(k), "text": v} for k, v in data.items()]
        if items is None and {"idx", "text"} <= set(data.keys()):
            items = [data]
    else:
        raise ValueError("unexpected translation root type")

    if not isinstance(items, list):
        raise ValueError("translations is not a list")

    by_idx: dict[int, str] = {}
    ordered_texts: list[str] = []
    for position, item in enumerate(items):
        text = _translation_item_text(item)
        if text is None:
            continue
        ordered_texts.append(text)
        if isinstance(item, str):
            if position < len(expected_idxs):
                by_idx[expected_idxs[position]] = text
            continue
        assert isinstance(item, dict)
        idx_raw = item.get("idx", item.get("index", item.get("id")))
        if idx_raw is None and position < len(expected_idxs):
            idx_raw = expected_idxs[position]
        if idx_raw is None:
            continue
        try:
            by_idx[int(idx_raw)] = text
        except (TypeError, ValueError):
            continue

    if not ordered_texts and not by_idx:
        raise ValueError("no translation items parsed")

    expected_set = set(expected_idxs)
    matched = expected_set & set(by_idx)
    missing = [idx for idx in expected_idxs if idx not in by_idx]

    # Full positional remap only when count matches and none/all idxs are off
    # (e.g. 1-based or relative 0..n). Never partially shuffle already-matched pairs.
    if missing and len(ordered_texts) == len(expected_idxs) and (
        not matched or len(matched) == 0
    ):
        return {
            expected_idxs[i]: ordered_texts[i] for i in range(len(expected_idxs))
        }

    if missing and len(ordered_texts) == len(expected_idxs) and matched:
        # Dense response with a shifted index range (e.g. 1..N for 0..N-1).
        returned = sorted(by_idx)
        if len(returned) == len(expected_idxs) and returned != expected_idxs:
            shifted = [x - returned[0] for x in returned]
            base = [x - expected_idxs[0] for x in expected_idxs]
            if shifted == base:
                return {
                    expected_idxs[i]: by_idx[returned[i]]
                    for i in range(len(expected_idxs))
                }

    # Leave holes empty; caller fills from source text. Do not reassign
    # ordered_texts[i] onto missing slots — that scrambles source/target pairs.
    return {idx: by_idx.get(idx, "") for idx in expected_idxs}


def _message_text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text") or part.get("content")
                if text:
                    parts.append(str(text))
        return "".join(parts).strip()
    if content is None:
        return ""
    return str(content).strip()


def _join_draft_text(left: str, right: str) -> str:
    left = (left or "").strip()
    right = (right or "").strip()
    if not left:
        return right
    if not right:
        return left
    if right[0] in ".,!?。？！、，":
        return f"{left}{right}"
    return f"{left} {right}"


def _merge_drafts_for_translation(
    drafts: list[tuple[int, int, str]],
    speaker_ids: list[str] | None = None,
) -> tuple[list[tuple[int, int, str]], list[str]]:
    """Merge abutting / incomplete scraps so translation stays 1:1 with meaning."""
    if not drafts:
        return [], []
    speakers = list(speaker_ids or ["speaker_0"] * len(drafts))
    if len(speakers) < len(drafts):
        speakers = speakers + ["speaker_0"] * (len(drafts) - len(speakers))
    if len(drafts) == 1:
        return drafts, speakers[:1]

    from .worker.utterance_pipeline import is_translation_dangling, looks_like_sentence_end

    max_gap_ms = max(0, int(os.getenv("TRANSLATION_MERGE_GAP_MS", "350")))
    # Keep enough room to finish a mid-sentence scrap; STAGE1_MAX may be lower
    # for display stamps and must not block translation merges.
    max_ms = max(
        15000,
        round(float(os.getenv("TRANSLATION_MERGE_MAX_SECONDS", "20")) * 1000),
    )
    merged_drafts: list[tuple[int, int, str]] = [drafts[0]]
    merged_speakers: list[str] = [speakers[0]]
    for index in range(1, len(drafts)):
        start, end, text = drafts[index]
        prev_start, prev_end, prev_text = merged_drafts[-1]
        gap = start - prev_end
        continuous = gap <= max_gap_ms
        incomplete = is_translation_dangling(prev_text) or (
            continuous and gap <= 80 and not looks_like_sentence_end(prev_text)
        )
        same_speaker = speakers[index] == merged_speakers[-1]
        if (
            continuous
            and incomplete
            and same_speaker
            and (end - prev_start) <= max_ms
        ):
            merged_drafts[-1] = (prev_start, end, _join_draft_text(prev_text, text))
        else:
            merged_drafts.append((start, end, text))
            merged_speakers.append(speakers[index])
    return merged_drafts, merged_speakers


def _translate(
    drafts: list[tuple[int, int, str]],
    source_language: str,
    target_language: str,
) -> list[str]:
    if not drafts:
        return []
    if source_language == target_language:
        return [text for _, _, text in drafts]
    base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("TRANSLATION_MODEL", "gpt-4o-mini")
    batch_size = max(1, int(os.getenv("TRANSLATION_BATCH_SIZE", "8")))
    src_name = LANGUAGE_NAMES.get(source_language, source_language)
    tgt_name = LANGUAGE_NAMES.get(target_language, target_language)
    results: list[str] = [""] * len(drafts)
    from .worker.locale_rules import (
        apply_translation_postprocess,
        translation_pair_rules,
    )

    pair_rules = translation_pair_rules(source_language, target_language)

    for batch_start in range(0, len(drafts), batch_size):
        batch = drafts[batch_start : batch_start + batch_size]
        expected_idxs = list(range(batch_start, batch_start + len(batch)))
        # Skip blank ASR scraps — keep source (empty) without calling the model.
        active = [
            (offset, start, end, text)
            for offset, (start, end, text) in enumerate(batch)
            if text.strip()
        ]
        if not active:
            continue

        active_expected = [batch_start + offset for offset, *_ in active]
        schema_payload = {
            "model": model,
            "temperature": 0.1,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "segment_translations",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "translations": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "idx": {"type": "integer"},
                                        "text": {"type": "string"},
                                    },
                                    "required": ["idx", "text"],
                                    "additionalProperties": False,
                                },
                            }
                        },
                        "required": ["translations"],
                        "additionalProperties": False,
                    },
                },
            },
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"Translate dubbing subtitles from {src_name} to "
                        f"{tgt_name}. Write natural native spoken language "
                        "for voice-over, retain all required diacritics, and spell "
                        "numbers or abbreviations as they should be spoken. "
                        "Each input segment is already a timing slot: translate "
                        "that segment alone. Never merge meaning across idxs, "
                        "never finish a previous segment inside the next idx, "
                        "and never omit or reorder. Return JSON: "
                        '{"translations":[{"idx":0,"text":"..."}]}.'
                        + (f"\n\n{pair_rules}" if pair_rules else "")
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "segments": [
                                {
                                    "idx": batch_start + offset,
                                    "text": text,
                                    "seconds": round((end - start) / 1000, 2),
                                }
                                for offset, start, end, text in active
                            ]
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        }
        json_object_payload = {
            **schema_payload,
            "response_format": {"type": "json_object"},
        }

        last_error: Exception | None = None
        parsed_ok = False
        for attempt in range(1, 4):
            # Prefer json_schema; after HTTP/schema rejection or parse failure,
            # fall back to plain json_object.
            body = schema_payload if attempt == 1 else json_object_payload
            try:
                response = httpx.post(
                    f"{base}/chat/completions",
                    headers={**_openai_headers(), "Content-Type": "application/json"},
                    json=body,
                    timeout=300,
                )
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt >= 3:
                    break
                time.sleep(attempt)
                continue
            if response.status_code >= 400:
                if attempt < 3 and response.status_code in {400, 404, 429}:
                    last_error = RuntimeError(
                        f"OpenAI 번역 실패 ({response.status_code}): {response.text[:300]}"
                    )
                    time.sleep(attempt)
                    continue
                raise RuntimeError(
                    f"OpenAI 번역 실패 ({response.status_code}): {response.text[:300]}"
                )
            try:
                message = response.json()["choices"][0]["message"]
                content = _message_text(message)
                if not content and message.get("refusal"):
                    raise ValueError(f"model refused: {message['refusal']}")
                parsed = _parse_translation_payload(content, active_expected)
                source_by_idx = {
                    batch_start + offset: text for offset, _s, _e, text in active
                }
                for idx in active_expected:
                    results[idx] = apply_translation_postprocess(
                        source_by_idx.get(idx, ""),
                        parsed.get(idx, ""),
                        source_language,
                        target_language,
                    )
                last_error = None
                parsed_ok = True
                break
            except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt >= 3:
                    break
                time.sleep(attempt)
        if not parsed_ok:
            # Retry each segment alone before falling back to source text.
            # Bulk parse failures used to copy source into target and scramble pairs.
            if len(active) > 1:
                for offset, start, end, text in active:
                    solo_idx = batch_start + offset
                    solo = _translate([(start, end, text)], source_language, target_language)
                    results[solo_idx] = solo[0] if solo else text
            else:
                for offset, (_start, _end, text) in enumerate(batch):
                    idx = batch_start + offset
                    if not results[idx]:
                        results[idx] = text
                if last_error is not None:
                    print(
                        f"[translate] batch {batch_start} fallback after parse error: "
                        f"{type(last_error).__name__}: {last_error}",
                        flush=True,
                    )

    if any(not text for text in results):
        # Fill any holes with source text rather than failing the whole upload.
        for idx, (_start, _end, text) in enumerate(drafts):
            if not results[idx]:
                results[idx] = text
    return results


def _process(
    source: Path,
    work_dir: Path,
    source_language: str,
    target_language: str,
    diarization_enabled: bool,
) -> dict:
    audio_wav = work_dir / "original_audio.wav"
    asr_mp3 = work_dir / "asr_audio.mp3"
    clips_dir = work_dir / "speech"
    clips_dir.mkdir()

    # One decode of the source: 48 kHz stereo PCM bed + compact 16 kHz ASR MP3.
    _run_ffmpeg(
        [
            "-i",
            str(source),
            "-vn",
            "-filter_complex",
            (
                "[0:a:0]asplit=2[bed][asr];"
                "[bed]aresample=48000[bed48];"
                "[asr]highpass=f=60,lowpass=f=7800,aresample=16000[asr16]"
            ),
            "-map",
            "[bed48]",
            "-c:a",
            "pcm_s16le",
            "-ac",
            "2",
            str(audio_wav),
            "-map",
            "[asr16]",
            "-c:a",
            "libmp3lame",
            "-ac",
            "1",
            "-b:a",
            "64k",
            str(asr_mp3),
        ]
    )

    # Step 2: word timestamps -> sentence/gap grouping -> matching audio clips.
    drafts, speech_ranges, asr_language = _transcribe(asr_mp3, source_language)
    if not drafts and os.getenv("ASR_VOCALS_RETRY", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        # Optional: TikTok/Shorts often bury speech under loud music.
        try:
            vocals, _ = _separate_no_vocals(work_dir)
            vocals_asr = work_dir / "asr_vocals.mp3"
            _run_ffmpeg(
                [
                    "-i",
                    str(vocals),
                    "-af",
                    "highpass=f=60,lowpass=f=7800",
                    "-c:a",
                    "libmp3lame",
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-b:a",
                    "64k",
                    str(vocals_asr),
                ]
            )
            drafts, speech_ranges, asr_language = _transcribe(
                vocals_asr, source_language
            )
            if drafts:
                asr_mp3 = vocals_asr
        except Exception:
            drafts = []
            speech_ranges = speech_ranges or []
    if not drafts:
        raise RuntimeError(
            "영상에서 인식 가능한 말(음성)을 찾지 못했습니다. "
            "원어 설정이 실제 영상 언어와 다르거나, 음악만 있는 영상일 수 있습니다. "
            "원어를 맞춘 뒤 말소리가 분명한 영상으로 다시 시도해 주세요."
        )
    if asr_language != source_language and asr_language in SUPPORTED_LANGUAGES:
        # Auto-detected a different supported language (common for TikTok).
        source_language = asr_language
    if diarization_enabled:
        turns = _openai_diarize(asr_mp3, source_language)
        # Normalize A/B → speaker_1/speaker_2 (first-appearance = 화자 1/2).
        label_map: dict[str, str] = {}
        normalized_turns: list[tuple[int, int, str, str]] = []
        for start, end, speaker, text in turns:
            raw = (speaker or "").strip() or "speaker_0"
            if raw not in label_map:
                label_map[raw] = f"speaker_{len(label_map) + 1}"
            normalized_turns.append((start, end, label_map[raw], text))
        turns = normalized_turns
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
        speaker_ids = ["speaker_1"] * len(drafts)
    drafts, speaker_ids = _merge_drafts_for_translation(drafts, speaker_ids)
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
        "asr_audio_path": asr_mp3.name,
        "asr_audio_url": f"/v1/local/step12/{work_dir.name}/{asr_mp3.name}",
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


def _process_and_publish(
    source: Path,
    work_dir: Path,
    source_language: str,
    target_language: str,
    diarization_enabled: bool,
) -> dict:
    manifest = _process(
        source,
        work_dir,
        source_language,
        target_language,
        diarization_enabled,
    )
    return _publish_run_to_r2(work_dir, manifest)


def _eleven_headers() -> dict[str, str]:
    key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY가 .env에 설정되지 않았습니다.")
    return {"xi-api-key": key}


_DUBBY_TEMP_VOICE_DESCRIPTION = "dubby:temp local verification voice"


def _is_dubby_temp_voice(voice: dict[str, object]) -> bool:
    """True for Instant Voice Clones created by this app (safe to auto-delete)."""
    category = str(voice.get("category") or "").lower()
    if category in {"premade", "professional", "famous"}:
        return False
    name = str(voice.get("name") or "")
    description = str(voice.get("description") or "")
    return (
        name.startswith("Dubby ")
        or "dubby:temp" in description.lower()
        or "Temporary local Dubby" in description
        or "Dubby per-project" in description
    )


def _list_eleven_voices() -> list[dict[str, object]]:
    base = os.getenv("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io").rstrip("/")
    response = httpx.get(
        f"{base}/v1/voices",
        headers=_eleven_headers(),
        timeout=60,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"ElevenLabs 보이스 목록 조회 실패 ({response.status_code}: {response.text[:200]})"
        )
    payload = response.json()
    voices = payload.get("voices") if isinstance(payload, dict) else None
    return [voice for voice in (voices or []) if isinstance(voice, dict)]


def _purge_stale_dubby_voices(*, keep_ids: set[str] | None = None) -> int:
    """Delete leftover Dubby temp clones so the custom-voice quota can recover."""
    keep = keep_ids or set()
    base = os.getenv("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io").rstrip("/")
    deleted = 0
    for voice in _list_eleven_voices():
        voice_id = str(voice.get("voice_id") or "").strip()
        if not voice_id or voice_id in keep:
            continue
        if not _is_dubby_temp_voice(voice):
            continue
        try:
            httpx.delete(
                f"{base}/v1/voices/{voice_id}",
                headers=_eleven_headers(),
                timeout=30,
            )
            deleted += 1
        except httpx.HTTPError:
            pass
    return deleted


def _eleven_voice_exists(voice_id: str) -> bool:
    base = os.getenv("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io").rstrip("/")
    response = httpx.get(
        f"{base}/v1/voices/{voice_id}",
        headers=_eleven_headers(),
        timeout=30,
    )
    return response.status_code < 400


def _eleven_request(
    method: str,
    url: str,
    *,
    label: str,
    retries: int | None = None,
    backoff_seconds: float | None = None,
    **kwargs,
) -> httpx.Response:
    """Call ElevenLabs with retries for transient 429/5xx failures."""
    attempts = retries
    if attempts is None:
        attempts = max(1, int(os.getenv("PIPELINE_STEP_RETRIES", "3")) + 1)
    delay = backoff_seconds
    if delay is None:
        delay = float(os.getenv("PIPELINE_RETRY_BACKOFF_SECONDS", "2"))
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            response = httpx.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            last_error = str(exc)
            if attempt >= attempts:
                raise RuntimeError(f"ElevenLabs {label} 요청 실패: {exc}") from exc
            time.sleep(delay * attempt)
            continue
        if response.status_code < 400:
            return response
        last_error = f"{response.status_code}: {response.text[:300]}"
        retryable = response.status_code == 429 or response.status_code >= 500
        if not retryable or attempt >= attempts:
            raise RuntimeError(f"ElevenLabs {label} 실패 ({last_error})")
        time.sleep(delay * attempt)
    raise RuntimeError(f"ElevenLabs {label} 실패 ({last_error})")


def _is_voice_slot_limit_error(message: str) -> bool:
    """Concurrent custom-voice slot cap (e.g. 30 voices), not monthly ops."""
    lowered = message.lower()
    if "voice_add_edit_limit_reached" in lowered:
        return False
    return (
        "voice_limit_reached" in lowered
        or "maximum amount of custom voices" in lowered
        or "custom voice limit" in lowered
    )


def _is_voice_add_edit_limit_error(message: str) -> bool:
    """Monthly Instant Voice Clone add/edit operation quota."""
    lowered = message.lower()
    return (
        "voice_add_edit_limit_reached" in lowered
        or "monthly limit of voice add/edit" in lowered
        or "voice add/edit operations" in lowered
    )


def _pick_reusable_dubby_voice(
    *,
    prefer_ids: set[str] | None = None,
) -> str | None:
    """Reuse an existing Dubby temp clone when creating a new one is blocked."""
    prefer = prefer_ids or set()
    voices = _list_eleven_voices()
    for voice_id in prefer:
        for voice in voices:
            if str(voice.get("voice_id") or "").strip() == voice_id:
                return voice_id
    for voice in voices:
        voice_id = str(voice.get("voice_id") or "").strip()
        if voice_id and _is_dubby_temp_voice(voice):
            return voice_id
    configured = os.getenv("ELEVENLABS_VOICE_ID", "").strip()
    if configured:
        return configured
    for voice in voices:
        category = str(voice.get("category") or "").lower()
        voice_id = str(voice.get("voice_id") or "").strip()
        if voice_id and category not in {"premade", "professional", "famous"}:
            return voice_id
    for voice in voices:
        voice_id = str(voice.get("voice_id") or "").strip()
        if voice_id:
            return voice_id
    return None


def _prepare_voice_sample(work_dir: Path, speaker_id: str | None = None) -> Path:
    """Build the clone sample from the Demucs vocals stem.

    Cutting the sample from the raw mix contaminates the cloned voice with
    background music and ambience, which makes every generated dub sound
    dirty. The vocals stem keeps only the speaker.
    """
    # ElevenLabs Instant Voice Clone rejects samples shorter than 1 second.
    min_seconds = float(os.getenv("VOICE_CLONE_MIN_SECONDS", "1.2"))
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

    speech_ranges = [
        (int(item["start_ms"]), int(item["end_ms"]))
        for item in manifest.get("speech_ranges") or []
        if int(item.get("end_ms", 0)) > int(item.get("start_ms", 0))
    ]

    candidate_ranges: list[tuple[int, int]] = []
    for segment in segments:
        start = int(segment["start_ms"])
        end = int(segment["end_ms"])
        if end <= start:
            continue
        overlaps = [
            (max(start, s), min(end, e))
            for s, e in speech_ranges
            if s < end and e > start
        ]
        overlaps = [(s, e) for s, e in overlaps if e - s >= 80]
        if overlaps:
            candidate_ranges.extend(overlaps)
        else:
            candidate_ranges.append((start, end))

    if not candidate_ranges:
        raise RuntimeError("보이스 샘플로 사용할 음성 구간이 없습니다.")

    vocals, _ = _separate_no_vocals(work_dir)
    vocals_duration_ms = max(1, int(_audio_duration(vocals) * 1000))
    max_seconds = float(os.getenv("VOICE_CLONE_SAMPLE_SECONDS", "60"))
    trims: list[str] = []
    labels: list[str] = []
    total = 0.0
    for index, (start_ms, end_ms) in enumerate(candidate_ranges):
        start = max(0.0, start_ms / 1000)
        end = end_ms / 1000
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

    if total < min_seconds:
        # Extend from the first candidate so ElevenLabs always gets >= 1s.
        start_ms, end_ms = candidate_ranges[0]
        start = max(0.0, start_ms / 1000)
        need = min_seconds
        end = min(vocals_duration_ms / 1000, start + need)
        if end - start < min_seconds:
            # Slide window earlier if near the end of the file.
            end = min(vocals_duration_ms / 1000, max(end, min_seconds))
            start = max(0.0, end - min_seconds)
        if end - start < min_seconds:
            raise RuntimeError(
                "보이스 클론용 샘플이 1초 미만입니다. "
                "더 긴 발화 구간이 있는 영상으로 다시 시도해 주세요."
            )
        trims = [
            f"[0:a]atrim=start={start:.3f}:end={end:.3f},"
            f"asetpts=PTS-STARTPTS[s0]"
        ]
        labels = ["[s0]"]
        total = end - start

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
    duration = _audio_duration(sample)
    if duration < 1.0:
        raise RuntimeError(
            f"보이스 클론용 샘플이 너무 짧습니다 ({duration:.2f}초). "
            "ElevenLabs는 최소 1초가 필요합니다."
        )
    return sample


def _create_eleven_voice(
    work_dir: Path,
    speaker_id: str | None = None,
    *,
    keep_voice_ids: set[str] | None = None,
) -> tuple[str, bool]:
    configured = os.getenv("ELEVENLABS_VOICE_ID", "").strip()
    if configured:
        return configured, False
    base = os.getenv("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io").rstrip("/")
    sample = _prepare_voice_sample(work_dir, speaker_id)

    def _add_voice() -> httpx.Response:
        with sample.open("rb") as audio:
            return _eleven_request(
                "POST",
                f"{base}/v1/voices/add",
                label="보이스 클론",
                headers=_eleven_headers(),
                data={
                    "name": f"Dubby {work_dir.name[:8]} {speaker_id or 'default'}",
                    "description": _DUBBY_TEMP_VOICE_DESCRIPTION,
                },
                files={"files": (sample.name, audio, "audio/mpeg")},
                timeout=300,
            )

    try:
        response = _add_voice()
    except RuntimeError as exc:
        message = str(exc)
        if _is_voice_add_edit_limit_error(message):
            # Creating/editing is blocked for the month — never purge+recreate
            # (that spends the same quota). Reuse any leftover Dubby temp voice.
            reused = _pick_reusable_dubby_voice(prefer_ids=keep_voice_ids)
            if reused:
                return reused, True
            raise RuntimeError(
                "ElevenLabs 월간 보이스 추가/수정 한도(예: 95회)에 도달했습니다. "
                "재사용할 Dubby 임시 보이스도 없습니다. "
                "api/.env에 ELEVENLABS_VOICE_ID를 설정해 클론을 건너뛰거나, "
                "요금제를 업그레이드하거나, 한도가 리셋될 때까지 기다려 주세요."
            ) from exc
        if not _is_voice_slot_limit_error(message):
            raise
        # Concurrent slot full — delete leftover Dubby temps, then retry once.
        purged = _purge_stale_dubby_voices(keep_ids=keep_voice_ids)
        if purged <= 0:
            reused = _pick_reusable_dubby_voice(prefer_ids=keep_voice_ids)
            if reused:
                return reused, True
            raise RuntimeError(
                "ElevenLabs 커스텀 보이스 한도(예: 30개)에 도달했습니다. "
                "ElevenLabs 대시보드에서 사용하지 않는 Instant Voice Clone을 삭제하거나 "
                "요금제를 업그레이드한 뒤 다시 시도해 주세요. "
                "개발 중이라면 .env에 ELEVENLABS_VOICE_ID를 설정해 클론을 건너뛸 수 있습니다."
            ) from exc
        try:
            response = _add_voice()
        except RuntimeError as retry_exc:
            if _is_voice_add_edit_limit_error(str(retry_exc)):
                reused = _pick_reusable_dubby_voice(prefer_ids=keep_voice_ids)
                if reused:
                    return reused, True
            raise

    voice_id = response.json().get("voice_id")
    if not voice_id:
        raise RuntimeError("ElevenLabs 응답에 voice_id가 없습니다.")
    return str(voice_id), True


def _load_cached_voices(work_dir: Path) -> dict[str, tuple[str, bool]]:
    path = work_dir / "dub_voice_manifest.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    cached: dict[str, tuple[str, bool]] = {}
    for speaker_id, info in (payload.get("voices") or {}).items():
        if not isinstance(info, dict):
            continue
        voice_id = str(info.get("voice_id") or "").strip()
        if voice_id:
            cached[str(speaker_id)] = (voice_id, bool(info.get("temporary", True)))
    return cached


def _resolve_usable_voices(
    work_dir: Path,
    speaker_ids: set[str],
    selected_voice_ids: list[str] | None = None,
) -> dict[str, tuple[str, bool]]:
    """Map speakers to selected Voice Box IDs; fall back to env voice (no clone)."""
    selected = [str(v).strip() for v in (selected_voice_ids or []) if str(v).strip()]
    env_voice = (os.getenv("ELEVENLABS_VOICE_ID") or "").strip()
    if not selected and env_voice:
        selected = [env_voice]
    if not selected:
        raise RuntimeError(
            "더빙 목소리가 없습니다. My Voice Box에서 목소리를 선택하거나 "
            "ELEVENLABS_VOICE_ID를 설정하세요."
        )

    ordered_speakers = sorted(speaker_ids) or ["speaker_0"]
    default = selected[0]
    voices: dict[str, tuple[str, bool]] = {}
    for index, speaker_id in enumerate(ordered_speakers):
        voice_id = selected[index] if index < len(selected) else default
        voices[speaker_id] = (voice_id, False)

    # Persist mapping for reruns (non-temporary — do not delete account voices).
    del work_dir  # mapping is derived from request each run
    return voices


def _delete_eleven_voices(voices: dict[str, tuple[str, bool]]) -> None:
    base = os.getenv("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io").rstrip("/")
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


def _cleanup_cached_voices(work_dir: Path) -> None:
    cached = _load_cached_voices(work_dir)
    if not cached:
        return
    _delete_eleven_voices(cached)
    path = work_dir / "dub_voice_manifest.json"
    if not path.is_file():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    payload.pop("voices", None)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


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
        idx: round(max(-14.0, min(14.0, level - reference)), 2)
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
    # Pre-measure unique ranges in parallel; shared helper then only does lookups.
    ranges_to_measure: set[tuple[int, int]] = set()
    for segment in manifest.get("segments") or []:
        idx = int(segment["idx"])
        if idx not in segment_indices:
            continue
        start_ms = int(segment["start_ms"])
        end_ms = int(segment["end_ms"])
        voiced = [
            (max(start_ms, start), min(end_ms, end))
            for start, end in speech_ranges
            if end > start_ms and start < end_ms
        ]
        if not voiced:
            voiced = [(start_ms, end_ms)]
        for range_start, range_end in voiced:
            if range_end > range_start:
                ranges_to_measure.add((range_start, range_end))
    measured: dict[tuple[int, int], float] = {}
    workers = max(1, min(8, len(ranges_to_measure)))
    if ranges_to_measure:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_mean_volume_db, vocals, start, end): (start, end)
                for start, end in ranges_to_measure
            }
            for future in as_completed(futures):
                start, end = futures[future]
                measured[(start, end)] = future.result()

    return _shared_source_loudness_levels(
        manifest.get("segments") or [],
        speech_ranges,
        segment_indices,
        lambda start_ms, end_ms: measured.get(
            (start_ms, end_ms),
            _mean_volume_db(vocals, start_ms, end_ms),
        ),
    )


def _generate_dub_voice(request: DubVoiceRequest) -> dict:
    work_dir = _resolve_work_dir(request.run_id)
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
    # Loudness prep can run while we resolve selected Voice Box IDs.
    with ThreadPoolExecutor(max_workers=2) as prep:
        voices_future = prep.submit(
            _resolve_usable_voices,
            work_dir,
            set(speaker_by_idx.values()),
            list(request.voice_ids or []),
        )
        levels_future = prep.submit(
            _source_loudness_levels,
            work_dir,
            {segment.idx for segment in request.segments},
        )
        voices = voices_future.result()
        source_levels = levels_future.result()
    from .worker.emotion import (
        detect_segment_emotion,
        normalize_emotion_tone,
        voice_settings_for_emotion,
    )

    project_tone = normalize_emotion_tone(request.tone_style)
    base = os.getenv("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io").rstrip("/")
    target_language = str(manifest.get("target_language") or "")
    model = tts_model_for_language(
        os.getenv("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5"),
        target_language,
    )
    output_dir = work_dir / "dubbed_speech"
    output_dir.mkdir(exist_ok=True)
    speak_min = float(
        os.getenv("TTS_SPEAK_SPEED_MIN", str(ELEVENLABS_SPEAK_SPEED_MIN))
    )
    speak_max = float(
        os.getenv("TTS_SPEAK_SPEED_MAX", str(ELEVENLABS_SPEAK_SPEED_MAX))
    )
    concurrency = max(1, int(os.getenv("TTS_CONCURRENCY", "4")))

    vocals_path = work_dir / "stems" / "htdemucs" / "original_audio" / "vocals.wav"
    if not vocals_path.is_file():
        # Fallback path used by some local scratch layouts.
        candidates = list(work_dir.glob("stems/**/vocals.wav"))
        vocals_path = candidates[0] if candidates else vocals_path

    def _synthesize_one(position: int, segment: DubSegment) -> dict[str, object]:
        filename = f"{segment.idx + 1:04d}.mp3"
        speaker_id = speaker_by_idx[segment.idx]
        voice_id = voices[speaker_id][0]
        source_meta = source_segments.get(segment.idx, {})
        start_ms = int(source_meta.get("start_ms", 0))
        end_ms = int(source_meta.get("end_ms", 0))
        source_text = str(source_meta.get("text") or source_meta.get("source_text") or "")
        if segment.emotion_tone:
            emotion_tone = normalize_emotion_tone(
                segment.emotion_tone, fallback=project_tone
            )
        elif vocals_path.is_file() and end_ms > start_ms:
            emotion_tone = detect_segment_emotion(
                str(vocals_path),
                start_ms=start_ms,
                end_ms=end_ms,
                source_text=source_text,
                fallback=project_tone,
            )
        else:
            emotion_tone = project_tone
        settings = voice_settings_for_emotion(emotion_tone)
        slot_seconds = max(
            0.001,
            (end_ms - start_ms) / 1000,
        )
        user_locked_speed = (
            segment.speak_speed is not None and float(segment.speak_speed) > 0
        )
        if user_locked_speed:
            speak_speed = min(
                max(float(segment.speak_speed), ELEVENLABS_SPEAK_SPEED_MIN),
                ELEVENLABS_SPEAK_SPEED_MAX,
            )
        else:
            speak_speed = initial_speak_speed(
                segment.target_text,
                slot_seconds,
                target_language,
                min_speed=speak_min,
                max_speed=speak_max,
            )
        tts_body: dict[str, object] = {
            "text": segment.target_text,
            "model_id": model,
            "voice_settings": {
                **settings,
                "use_speaker_boost": True,
                "speed": min(
                    max(speak_speed, ELEVENLABS_SPEAK_SPEED_MIN),
                    ELEVENLABS_SPEAK_SPEED_MAX,
                ),
            },
            "apply_text_normalization": "on",
        }
        if target_language and model != "eleven_multilingual_v2":
            tts_body["language_code"] = target_language.lower().split("-", 1)[0]
        if position > 0:
            tts_body["previous_text"] = request.segments[position - 1].target_text
        if position + 1 < len(request.segments):
            tts_body["next_text"] = request.segments[position + 1].target_text
        response = _eleven_request(
            "POST",
            f"{base}/v1/text-to-speech/{voice_id}",
            label="TTS",
            params={"output_format": "mp3_44100_128"},
            headers={**_eleven_headers(), "Content-Type": "application/json"},
            json=tts_body,
            timeout=300,
        )
        output_path = output_dir / filename
        output_path.write_bytes(response.content)
        duration = _audio_duration(output_path)
        # Auto-correct only when the editor did not lock a speak rate.
        if not user_locked_speed:
            measured_speed = speak_speed_for_slot(
                duration,
                slot_seconds,
                min_speed=speak_min,
                max_speed=speak_max,
            )
            if abs(measured_speed - speak_speed) >= 0.12 and measured_speed > 1.03:
                tts_body["voice_settings"] = {
                    **settings,
                    "use_speaker_boost": True,
                    "speed": min(
                        max(measured_speed, ELEVENLABS_SPEAK_SPEED_MIN),
                        ELEVENLABS_SPEAK_SPEED_MAX,
                    ),
                }
                response = _eleven_request(
                    "POST",
                    f"{base}/v1/text-to-speech/{voice_id}",
                    label="TTS",
                    params={"output_format": "mp3_44100_128"},
                    headers={**_eleven_headers(), "Content-Type": "application/json"},
                    json=tts_body,
                    timeout=300,
                )
                output_path.write_bytes(response.content)
                duration = _audio_duration(output_path)
                speak_speed = measured_speed
        tts_level = _mean_volume_db(
            output_path,
            0,
            max(1, int(duration * 1000)),
        )
        source_level = source_levels.get(segment.idx, tts_level)
        gain_db = _matched_loudness_gain(source_level, tts_level)
        # Bake gain into the preview file so editor playback matches the mix.
        _apply_preview_gain(output_path, gain_db)
        return {
            "idx": segment.idx,
            "speaker_id": speaker_id,
            "target_text": segment.target_text,
            "source_level_db": source_level,
            "tts_level_db": tts_level,
            "gain_db": gain_db,
            "preview_gain_applied": True,
            "emotion_tone": emotion_tone,
            "speak_speed": (
                float(segment.speak_speed)
                if user_locked_speed
                else (speak_speed if speak_speed > 1.03 else 1.0)
            ),
            "audio_url": (
                f"/v1/local/step12/{request.run_id}/dubbed_speech/{filename}"
            ),
        }

    outputs: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=min(concurrency, max(1, len(request.segments)))) as pool:
        futures = [
            pool.submit(_synthesize_one, position, segment)
            for position, segment in enumerate(request.segments)
        ]
        for future in as_completed(futures):
            outputs.append(future.result())
    outputs.sort(key=lambda item: int(item["idx"]))

    manifest_path = work_dir / "dub_voice_manifest.json"
    prior_segments: dict[int, dict] = {}
    prior_voices: dict = {}
    if manifest_path.is_file():
        try:
            prior = json.loads(manifest_path.read_text(encoding="utf-8"))
            prior_voices = prior.get("voices") or {}
            for row in prior.get("segments") or []:
                if isinstance(row, dict) and "idx" in row:
                    prior_segments[int(row["idx"])] = row
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    for output in outputs:
        prior_segments[int(output["idx"])] = {
            key: value for key, value in output.items() if key != "audio_url"
        }
    merged_voices = {
        **prior_voices,
        **{
            speaker_id: {
                "voice_id": voice_id,
                "temporary": temporary,
            }
            for speaker_id, (voice_id, temporary) in voices.items()
        },
    }
    manifest_path.write_text(
        json.dumps(
            {
                "voices": merged_voices,
                "segments": [
                    prior_segments[idx] for idx in sorted(prior_segments.keys())
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _finalize_run_upload(request.run_id, work_dir)
    for output in outputs:
        output["audio_url"] = _local_asset_url(
            request.run_id,
            f"dubbed_speech/{int(output['idx']) + 1:04d}.mp3",
        )
    return {"run_id": request.run_id, "segments": outputs}


def _source_file(work_dir: Path) -> Path:
    sources = sorted(work_dir.glob("source.*"))
    if not sources:
        raise RuntimeError("원본 영상 파일을 찾을 수 없습니다.")
    return sources[0]


def _demucs_model() -> str:
    return os.getenv("DEMUCS_MODEL", "htdemucs").strip() or "htdemucs"


def _demucs_device() -> str:
    configured = os.getenv("DEMUCS_DEVICE", "").strip()
    if configured:
        return configured
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def _demucs_jobs() -> str:
    configured = os.getenv("DEMUCS_JOBS", "").strip()
    if configured:
        return configured
    cpus = os.cpu_count() or 2
    return str(min(4, max(1, cpus // 2)))


def _separate_no_vocals(work_dir: Path) -> tuple[Path, Path]:
    audio = work_dir / "original_audio.wav"
    if not audio.is_file():
        raise RuntimeError("먼저 오디오·자막 추출을 실행해 주세요.")
    model = _demucs_model()
    stem_root = work_dir / "stems"
    stem_dir = stem_root / model / audio.stem
    vocals = stem_dir / "vocals.wav"
    no_vocals = stem_dir / "no_vocals.wav"
    lock = _demucs_locks.setdefault(work_dir.name, threading.Lock())
    with lock:
        if vocals.is_file() and no_vocals.is_file():
            return vocals, no_vocals
        _run_command(
            [
                sys.executable,
                "-m",
                "app.demucs_separate_shim",
                "-n",
                model,
                "--two-stems",
                "vocals",
                "-d",
                _demucs_device(),
                "-j",
                _demucs_jobs(),
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
    *,
    no_vocals_in_mask: float = 0.35,
) -> None:
    """Remove vocals only while ASR-recognized language is present.

    Outside those timestamps the original waveform is passed through
    unchanged, preserving cheers, crying, singing, music, and ambience.
    """
    mask = _speech_mask_expression(ranges_ms)
    bleed = max(0.0, min(1.0, float(no_vocals_in_mask)))
    filters = (
        f"[0:a]aresample=44100,volume=eval=frame:volume='1-({mask})'[original];"
        f"[1:a]aresample=44100,volume=eval=frame:volume='({mask})*{bleed:.4f}'[removed];"
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


def _apply_preview_gain(path: Path, gain_db: float) -> None:
    """Bake loudness match into the on-disk preview clip (idempotent-ish)."""
    if abs(float(gain_db)) < 0.05:
        return
    tmp = path.with_suffix(path.suffix + ".gain.tmp")
    try:
        _run_ffmpeg(
            [
                "-i",
                str(path),
                "-af",
                f"volume={float(gain_db):.2f}dB",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "128k",
                str(tmp),
            ]
        )
        tmp.replace(path)
    finally:
        if tmp.is_file():
            tmp.unlink(missing_ok=True)


def _fit_dub_clip(
    source: Path,
    output: Path,
    slot_seconds: float,
    gain_db: float = 0.0,
) -> tuple[float, bool]:
    duration = _audio_duration(source)
    if duration <= 0 or slot_seconds <= 0:
        raise RuntimeError(f"유효하지 않은 더빙 구간입니다: {source.name}")
    # Fit speech inside its non-overlapping timestamp slot. Prefer pitch-
    # preserving rubberband; fall back to atempo only when unavailable.
    max_speedup = float(os.getenv("TTS_MAX_SPEEDUP", "1.5"))
    min_tempo = float(os.getenv("TTS_MIN_TEMPO", "0.85"))
    requested = duration / slot_seconds
    tempo = min(max(requested, min_tempo), max_speedup)
    audible = min(slot_seconds, duration / tempo)
    fade = min(0.2, audible / 2)
    filters = tempo_filters(tempo, rubberband_available=_ffmpeg_has_rubberband())
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
    return tempo, requested > max_speedup


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
    work_dir = _resolve_work_dir(request.run_id)
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
    word_ranges = [
        (int(item["start_ms"]), int(item["end_ms"]))
        for item in saved_ranges
        if int(item.get("end_ms", 0)) > int(item.get("start_ms", 0))
    ]
    segment_bounds = [
        (segment.start_ms, segment.end_ms)
        for segment in ordered
        if segment.source_text.strip() and segment.end_ms > segment.start_ms
    ]
    # Final dub must scrub solid dialogue windows (no mid-phrase original bleed).
    speech_ranges = _voice_removal_ranges(
        word_ranges,
        segment_bounds,
        fill_interiors=True,
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

    # Selective bed is required for the final mix; skip the inspection
    # voice_removed.mp4 mux (slow and unused in the mobile editor).

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
        int(item["idx"]): (
            0.0
            if item.get("preview_gain_applied")
            else float(item.get("gain_db", 0.0))
        )
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
                f"segment_{segment.idx}: 최대 배속으로도 길어 구간 끝에서 잘렸습니다."
            )
        elif tempo > 1.15:
            backend = "rubberband" if _ffmpeg_has_rubberband() else "atempo"
            warnings.append(
                f"segment_{segment.idx}: 피치 유지({backend})로 "
                f"{tempo:.2f}배 길이를 맞췄습니다."
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
    source_name = source.name
    # Keep cloned voices + stems on R2 so users can edit subtitles and
    # regenerate dub voice without re-extracting the whole run.
    _finalize_final_outputs(request.run_id, work_dir, source)
    return {
        "run_id": request.run_id,
        "source_url": _local_asset_url(request.run_id, source_name),
        "output_url": _local_asset_url(request.run_id, "dubbed_output.mp4"),
        "warnings": warnings,
    }


app = FastAPI(title="Dubby local step 1-2 verifier")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=(
        r"https?://(localhost|127\.0\.0\.1)(:\d+)?"
        r"|https://([\w-]+\.)?github\.io"
        r"|https://([\w-]+\.)?pages\.dev"
    ),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Filename", "Authorization"],
)


def _http_remote_media_error(exc: RemoteMediaError) -> HTTPException:
    message = str(exc)
    status = 400
    if "yt-dlp가 설치" in message:
        status = 503
    elif "너무 큽니다" in message:
        status = 413
    return HTTPException(status, message)


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "ok": True,
        "mode": "local-step12",
        "model": os.getenv("LOCAL_WHISPER_MODEL", "medium"),
        "openai_configured": bool(os.getenv("OPENAI_API_KEY", "").strip()),
        "elevenlabs_configured": bool(os.getenv("ELEVENLABS_API_KEY", "").strip()),
        "r2_configured": bool(
            os.getenv("R2_ACCOUNT_ID", "").strip()
            and os.getenv("R2_ACCESS_KEY_ID", "").strip()
            and os.getenv("R2_SECRET_ACCESS_KEY", "").strip()
        ),
        "media_storage": "r2",
    }


@app.post("/v1/local/step12")
async def create_step12(
    request: Request,
    source_lang: Annotated[str, Query(pattern=LANG_QUERY_PATTERN)] = "ko",
    target_lang: Annotated[str, Query(pattern=LANG_QUERY_PATTERN)] = "en",
    diarization_enabled: Annotated[bool, Query()] = False,
    x_filename: Annotated[str, Header()] = "source.mp4",
) -> dict:
    if source_lang not in SUPPORTED_LANGUAGES:
        raise HTTPException(400, "지원하지 않는 원어입니다.")
    run_id = uuid4().hex
    work_dir = _scratch_dir(run_id)
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
            _process_and_publish,
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


@app.post("/v1/local/step12/from-url")
async def create_step12_from_url(
    body: FromUrlRequest,
    source_lang: Annotated[str, Query(pattern=LANG_QUERY_PATTERN)] = "ko",
    target_lang: Annotated[str, Query(pattern=LANG_QUERY_PATTERN)] = "en",
    diarization_enabled: Annotated[bool, Query()] = False,
) -> dict:
    """Ingest a YouTube/Facebook/TikTok page URL or direct MP4/WebM link."""
    if source_lang not in SUPPORTED_LANGUAGES:
        raise HTTPException(400, "지원하지 않는 원어입니다.")
    run_id = uuid4().hex
    work_dir = _scratch_dir(run_id)
    work_dir.mkdir(parents=True, exist_ok=False)
    try:
        source = await ingest_remote_media(
            body.url.strip(),
            work_dir,
            max_bytes=MAX_SOURCE_BYTES,
        )
        # yt-dlp path is sync inside ingest; wrap full process off the event loop.
        return await asyncio.to_thread(
            _process_and_publish,
            source,
            work_dir,
            source_lang,
            target_lang,
            diarization_enabled,
        )
    except RemoteMediaError as exc:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise _http_remote_media_error(exc) from exc
    except HTTPException:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
    except Exception as exc:
        raise HTTPException(
            500,
            {
                "message": str(exc),
                "run_id": run_id,
                "work_dir": str(work_dir),
            },
        ) from exc


@app.post("/v1/local/retranslate")
async def retranslate_segments(body: RetranslateRequest) -> dict:
    try:
        drafts = [
            (seg.start_ms, seg.end_ms, seg.source_text.strip())
            for seg in body.segments
        ]
        translations = await asyncio.to_thread(
            _translate, drafts, body.source_lang, body.target_lang
        )
        return {
            "segments": [
                {"idx": seg.idx, "target_text": translations[offset]}
                for offset, seg in enumerate(body.segments)
            ]
        }
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@app.delete("/v1/local/runs/{run_id}")
async def delete_local_run(run_id: str) -> dict:
    """Permanently delete one local pipeline run (scratch + R2). No recovery."""
    safe_id = _assert_run_id(run_id)
    return await asyncio.to_thread(_delete_run_storage, safe_id)


@app.post("/v1/local/runs/gc")
async def gc_local_runs(body: GcRunsRequest) -> dict:
    """Delete every local/R2 run that is not in ``keep_run_ids``."""
    keep = {_assert_run_id(run_id) for run_id in body.keep_run_ids if run_id.strip()}
    return await asyncio.to_thread(_gc_orphan_runs, keep)


_USER_ID_RE = re.compile(r"^[0-9a-fA-F-]{8,64}$")


def _assert_user_id(user_id: str) -> str:
    cleaned = (user_id or "").strip()
    if not _USER_ID_RE.fullmatch(cleaned):
        raise HTTPException(400, "유효하지 않은 사용자 id 입니다.")
    return cleaned


def _demo_state_path(user_id: str) -> Path:
    return DEMO_STATE_ROOT / f"{user_id}.json"


def _read_demo_state(user_id: str) -> dict[str, object] | None:
    path = _demo_state_path(user_id)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _write_demo_state(user_id: str, payload: dict[str, object]) -> None:
    DEMO_STATE_ROOT.mkdir(parents=True, exist_ok=True)
    path = _demo_state_path(user_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


@app.get("/v1/local/demo-state/{user_id}")
async def get_demo_state(user_id: str) -> dict[str, object]:
    """Shared local-pipeline history for phone + PC (same Supabase user)."""
    safe = _assert_user_id(user_id)
    payload = await asyncio.to_thread(_read_demo_state, safe)
    if payload is None:
        return {"ok": True, "empty": True, "state": None}
    return {"ok": True, "empty": False, "state": payload}


@app.put("/v1/local/demo-state/{user_id}")
async def put_demo_state(user_id: str, body: dict[str, object]) -> dict[str, object]:
    """Persist demo/local history so devices sharing the tunnel stay in sync."""
    safe = _assert_user_id(user_id)
    if not isinstance(body, dict):
        raise HTTPException(400, "state 본문이 올바르지 않습니다.")
    state = body.get("state")
    if not isinstance(state, dict):
        raise HTTPException(400, "state 객체가 필요합니다.")
    await asyncio.to_thread(_write_demo_state, safe, state)
    return {"ok": True}


_local_jobs_lock = threading.Lock()
_local_jobs: dict[str, dict[str, object]] = {}


def _set_local_job(job_id: str, **fields: object) -> None:
    with _local_jobs_lock:
        current = dict(_local_jobs.get(job_id) or {"job_id": job_id})
        current.update(fields)
        current["job_id"] = job_id
        current["updated_at"] = time.time()
        _local_jobs[job_id] = current


def _get_local_job(job_id: str) -> dict[str, object] | None:
    with _local_jobs_lock:
        job = _local_jobs.get(job_id)
        return dict(job) if job else None


def _start_local_job(kind: str, worker) -> dict[str, object]:
    job_id = uuid4().hex
    _set_local_job(
        job_id,
        kind=kind,
        status="running",
        created_at=time.time(),
    )

    def _run() -> None:
        try:
            result = worker()
            _set_local_job(job_id, status="done", result=result, error=None)
        except Exception as exc:  # noqa: BLE001 - surfaced to the poller
            _set_local_job(job_id, status="error", error=str(exc), result=None)

    threading.Thread(target=_run, name=f"local-{kind}-{job_id[:8]}", daemon=True).start()
    return {"job_id": job_id, "status": "running", "kind": kind}


@app.post("/v1/local/dub-voice")
async def create_dub_voice(body: DubVoiceRequest) -> dict:
    # Return immediately and finish TTS in the background. Long sync requests
    # through Cloudflare Tunnel / mobile browsers are canceled as "Failed to fetch".
    return _start_local_job("dub-voice", lambda: _generate_dub_voice(body))


@app.post("/v1/local/render-dub")
async def render_dub(body: RenderDubRequest) -> dict:
    return _start_local_job("render-dub", lambda: _render_dubbed_video(body))


@app.get("/v1/local/jobs/{job_id}")
async def get_local_job(job_id: str) -> dict:
    job = _get_local_job(job_id)
    if not job:
        raise HTTPException(404, "작업을 찾을 수 없습니다.")
    return {
        "job_id": job.get("job_id"),
        "kind": job.get("kind"),
        "status": job.get("status"),
        "error": job.get("error"),
        "result": job.get("result"),
    }


@app.get("/v1/local/step12/{run_id}/{asset_path:path}", response_model=None)
async def get_asset(
    run_id: str,
    asset_path: str,
    download: Annotated[str | None, Query(max_length=200)] = None,
) -> Response:
    root = (_scratch_dir(run_id)).resolve()
    candidate = (root / asset_path).resolve()
    if root in candidate.parents and candidate.is_file():
        if download is not None:
            filename = Path(download).name or candidate.name
            return FileResponse(
                candidate,
                filename=filename,
                content_disposition_type="attachment",
            )
        return FileResponse(candidate)
    try:
        store = LocalR2Store()
        filename = Path(download).name if download else None
        if download is not None:
            # Proxy through this origin with Content-Disposition so browsers
            # save the file instead of playing the redirected R2 object.
            body = await asyncio.to_thread(store.get_object_bytes, run_id, asset_path)
            safe_name = filename or Path(asset_path).name or "download.bin"
            return Response(
                content=body,
                media_type="application/octet-stream",
                headers={
                    "Content-Disposition": f'attachment; filename="{safe_name}"',
                    "Content-Length": str(len(body)),
                },
            )
        url = store.presign_get(run_id, asset_path)
        return RedirectResponse(url, status_code=307)
    except Exception as exc:
        raise HTTPException(404, "결과 파일을 찾을 수 없습니다.") from exc
