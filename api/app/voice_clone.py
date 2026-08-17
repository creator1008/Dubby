"""Instant Voice Clone helpers for My Voice Box uploads."""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

from .config import Settings
from .errors import BadRequestError, FeatureUnavailableError
from .languages import SUPPORTED_LANGUAGES
from .storage.r2 import R2Storage
from .voices_elevenlabs import GENDER_OPTIONS
from .worker.elevenlabs_client import ElevenLabsClient

logger = logging.getLogger("dubby.voice_clone")

CLONE_NICKNAME_STAR = "★"
# Reject near-empty media; short clips (≤1 min) are cloned in full.
CLONE_MIN_SECONDS = 1.0
# Longer uploads are truncated to the first 3 minutes for IVC.
CLONE_MAX_SECONDS = 180.0
CLONE_MAX_UPLOAD_BYTES = 500 * 1024 * 1024
IVC_SHARED_PREFIX = "ivc:"
IVC_PUBLIC_OWNER = "dubby:ivc"
VOICEBOX_CLONE_DESCRIPTION = "dubby:voicebox instant voice clone"
# First-sentence preview: strip leading silence, keep ~one short utterance.
PREVIEW_MAX_SECONDS = 6.0


def starred_nickname(nickname: str) -> str:
    cleaned = (nickname or "").strip()
    if cleaned.startswith(CLONE_NICKNAME_STAR):
        return cleaned[:30]
    return f"{CLONE_NICKNAME_STAR}{cleaned}"[:30]


def is_cloned_voice_row(row: dict) -> bool:
    shared = str(row.get("shared_voice_id") or "")
    owner = str(row.get("public_owner_id") or "")
    return shared.startswith(IVC_SHARED_PREFIX) or owner == IVC_PUBLIC_OWNER


def voice_preview_key(user_id: UUID, voice_row_id: UUID) -> str:
    return f"users/{user_id}/voices/{voice_row_id}/preview.mp3"


async def _run_cmd(cmd: list[str], *, error: str) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        detail = (err or out or b"").decode("utf-8", "replace")[:300]
        raise BadRequestError(f"{error}: {detail or 'command failed'}")
    return out


async def probe_duration_seconds(settings: Settings, path: Path) -> float:
    cmd = [
        settings.ffprobe_path,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    raw = await _run_cmd(cmd, error="미디어 길이를 확인할 수 없습니다")
    try:
        data = json.loads(raw.decode("utf-8"))
        duration = float((data.get("format") or {}).get("duration") or 0)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise BadRequestError("미디어 메타데이터를 읽을 수 없습니다.") from exc
    streams = data.get("streams") or []
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    if not has_audio:
        raise BadRequestError("오디오 트랙이 있는 파일만 복제할 수 있습니다.")
    return duration


def validate_clone_duration(duration: float) -> None:
    if duration < CLONE_MIN_SECONDS:
        raise BadRequestError("복제용 파일에 유효한 오디오가 없습니다.")


def clone_sample_seconds(duration: float) -> float:
    """Use the full clip when short; cap at 3 minutes when longer."""
    if duration <= 0:
        return 0.0
    return min(float(duration), CLONE_MAX_SECONDS)


async def extract_clone_sample(
    settings: Settings,
    source: Path,
    dest_mp3: Path,
    *,
    max_seconds: float,
) -> None:
    """Mono MP3 sample for ElevenLabs Instant Voice Clone (first N seconds)."""
    seconds = max(CLONE_MIN_SECONDS, float(max_seconds))
    cmd = [
        settings.ffmpeg_path,
        "-y",
        "-nostdin",
        "-i",
        str(source),
        "-vn",
        "-t",
        f"{seconds:.3f}",
        "-ac",
        "1",
        "-ar",
        "44100",
        "-b:a",
        "128k",
        str(dest_mp3),
    ]
    await _run_cmd(cmd, error="복제용 오디오 추출에 실패했습니다")


async def extract_first_sentence_preview(
    settings: Settings, source: Path, dest_mp3: Path
) -> None:
    """Strip leading silence and keep a short first-utterance preview."""
    cmd = [
        settings.ffmpeg_path,
        "-y",
        "-nostdin",
        "-i",
        str(source),
        "-vn",
        "-af",
        "silenceremove=start_periods=1:start_duration=0.15:start_threshold=-35dB",
        "-t",
        f"{PREVIEW_MAX_SECONDS:.2f}",
        "-ac",
        "1",
        "-ar",
        "44100",
        "-b:a",
        "128k",
        str(dest_mp3),
    ]
    await _run_cmd(cmd, error="미리듣기 오디오 추출에 실패했습니다")


async def clone_voice_into_box(
    *,
    settings: Settings,
    storage: R2Storage,
    owner_id: UUID,
    nickname: str,
    language: str,
    gender: str,
    upload_path: Path,
    upload_size: int,
) -> dict:
    """Run IVC, upload preview, return fields for ``add_user_voice``."""
    if not settings.elevenlabs_api_key:
        raise FeatureUnavailableError("ElevenLabs API key is not configured")

    lang = (language or "").strip().lower()
    if lang not in SUPPORTED_LANGUAGES:
        raise BadRequestError("지원하지 않는 언어입니다.")
    gender_norm = (gender or "").strip().lower()
    if gender_norm not in GENDER_OPTIONS:
        raise BadRequestError("성별을 선택해 주세요.")
    if upload_size <= 0 or upload_size > CLONE_MAX_UPLOAD_BYTES:
        raise BadRequestError("파일 크기가 허용 범위를 벗어났습니다.")

    display_name = starred_nickname(nickname)
    if len(display_name.strip()) < 2:  # star + at least 1 char
        raise BadRequestError("별명을 입력해 주세요.")

    duration = await probe_duration_seconds(settings, upload_path)
    validate_clone_duration(duration)
    sample_seconds = clone_sample_seconds(duration)

    voice_row_id = uuid4()
    with tempfile.TemporaryDirectory(prefix="dubby-ivc-") as tmp:
        tmp_dir = Path(tmp)
        sample_mp3 = tmp_dir / "clone_sample.mp3"
        preview_mp3 = tmp_dir / "preview.mp3"
        await extract_clone_sample(
            settings, upload_path, sample_mp3, max_seconds=sample_seconds
        )
        await extract_first_sentence_preview(settings, upload_path, preview_mp3)

        client = ElevenLabsClient(settings)
        try:
            el_voice_id, used_fallback = await client.create_voice(
                str(sample_mp3),
                display_name,
                description=VOICEBOX_CLONE_DESCRIPTION,
                allow_limit_fallback=False,
            )
        finally:
            await client.aclose()
        if used_fallback:
            # Defensive — allow_limit_fallback=False should never return True.
            raise BadRequestError(
                "월간 목소리 생성 한도에 도달했습니다. 잠시 후 다시 시도해 주세요."
            )

        preview_key = voice_preview_key(owner_id, voice_row_id)
        await storage.upload_file(
            str(preview_mp3), preview_key, content_type="audio/mpeg"
        )

    return {
        "id": voice_row_id,
        "nickname": display_name,
        "elevenlabs_voice_id": el_voice_id,
        "shared_voice_id": f"{IVC_SHARED_PREFIX}{el_voice_id}",
        "public_owner_id": IVC_PUBLIC_OWNER,
        "name": display_name,
        "description": "Instant Voice Clone",
        "gender": gender_norm,
        "accent": "",
        "category": "cloned",
        "language": lang,
        "age": "",
        "preview_url": preview_key,
    }
