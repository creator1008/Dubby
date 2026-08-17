"""Voice Setting: ElevenLabs library browse + My Voice Box CRUD + IVC clone."""

from __future__ import annotations

import tempfile
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, File, Form, Query, UploadFile, status

from ..auth import CurrentUser
from ..config import get_settings
from ..db.base import DuplicateVoiceError
from ..deps import Repo, Storage
from ..errors import BadRequestError, ConflictError, NotFoundError
from ..schemas import (
    SharedVoiceOut,
    SharedVoicesOut,
    UserVoiceCreate,
    UserVoiceOut,
    VoiceFilterOptionsOut,
)
from ..voice_clone import (
    CLONE_MAX_UPLOAD_BYTES,
    clone_voice_into_box,
    is_cloned_voice_row,
    voice_preview_key,
)
from ..voices_elevenlabs import (
    ACCENTS_BY_LANGUAGE,
    AGE_OPTIONS,
    CATEGORY_OPTIONS,
    GENDER_OPTIONS,
    add_shared_voice_to_account,
    fetch_shared_voices,
)
from ..voice_translate import translate_descriptions
from ..worker.elevenlabs_client import ElevenLabsClient
from ..worker.errors import PipelineError

router = APIRouter(prefix="/v1/voices", tags=["voices"])


def _map_shared(raw: dict) -> SharedVoiceOut:
    # Prefer use_case for the UI "Category" column (Narration, etc.).
    use_case = str(raw.get("use_case") or "").strip()
    category = use_case or str(raw.get("category") or "")
    return SharedVoiceOut(
        public_owner_id=str(raw.get("public_owner_id") or ""),
        voice_id=str(raw.get("voice_id") or ""),
        name=str(raw.get("name") or ""),
        description=str(raw.get("description") or "") or None,
        gender=str(raw.get("gender") or ""),
        accent=str(raw.get("accent") or ""),
        category=category,
        language=str(raw.get("language") or "") or None,
        age=str(raw.get("age") or ""),
        preview_url=str(raw.get("preview_url") or "") or None,
    )


async def _sign_box_row(storage: Storage, row: dict) -> UserVoiceOut:
    out = UserVoiceOut.model_validate(row)
    preview = (out.preview_url or "").strip()
    if preview.startswith("users/"):
        try:
            signed = await storage.presign_get(preview, expires_in=60 * 60 * 24 * 7)
            out = out.model_copy(update={"preview_url": signed})
        except Exception:
            out = out.model_copy(update={"preview_url": None})
    return out


@router.get("/filters", response_model=VoiceFilterOptionsOut)
async def voice_filter_options(user: CurrentUser) -> VoiceFilterOptionsOut:
    _ = user
    return VoiceFilterOptionsOut(
        languages=sorted(ACCENTS_BY_LANGUAGE.keys()),
        accents_by_language=ACCENTS_BY_LANGUAGE,
        genders=GENDER_OPTIONS,
        ages=AGE_OPTIONS,
        categories=CATEGORY_OPTIONS,
    )


@router.get("/library", response_model=SharedVoicesOut)
async def list_voice_library(
    user: CurrentUser,
    page: int = Query(0, ge=0),
    page_size: int = Query(30, ge=1, le=100),
    language: str | None = Query(None),
    accent: str | None = Query(None),
    category: str | None = Query(None),
    gender: str | None = Query(None),
    age: str | None = Query(None),
    search: str | None = Query(None, max_length=100),
    ui_locale: str | None = Query(None, max_length=16),
) -> SharedVoicesOut:
    _ = user
    settings = get_settings()
    payload = await fetch_shared_voices(
        settings,
        page=page,
        page_size=page_size,
        language=language,
        accent=accent,
        category=category,
        gender=gender,
        age=age,
        search=search,
    )
    voices_raw = payload.get("voices") if isinstance(payload, dict) else None
    voices = [
        _map_shared(v)
        for v in (voices_raw or [])
        if isinstance(v, dict) and v.get("voice_id")
    ]
    locale = (ui_locale or "en").strip().lower()
    if locale and locale != "en" and voices:
        originals = [v.description or "" for v in voices]
        translated = await translate_descriptions(
            settings, originals, ui_locale=locale
        )
        voices = [
            voice.model_copy(update={"description": translated[idx] or None})
            for idx, voice in enumerate(voices)
        ]
    return SharedVoicesOut(
        voices=voices,
        has_more=bool(payload.get("has_more")),
        total_count=int(payload.get("total_count") or 0),
        page=page,
    )


@router.get("/box", response_model=list[UserVoiceOut])
async def list_voice_box(
    user: CurrentUser, repo: Repo, storage: Storage
) -> list[UserVoiceOut]:
    rows = await repo.list_user_voices(user.id)
    return [await _sign_box_row(storage, r) for r in rows]


@router.post(
    "/box",
    response_model=UserVoiceOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_to_voice_box(
    body: UserVoiceCreate, user: CurrentUser, repo: Repo, storage: Storage
) -> UserVoiceOut:
    nickname = body.nickname.strip()
    if not nickname:
        raise BadRequestError("Nickname is required")
    if len(nickname) > 30:
        raise BadRequestError("Nickname must be 30 characters or fewer")

    settings = get_settings()
    account_voice_id = await add_shared_voice_to_account(
        settings,
        public_owner_id=body.public_owner_id,
        voice_id=body.voice_id,
        new_name=nickname,
    )

    try:
        row = await repo.add_user_voice(
            user.id,
            {
                "nickname": nickname,
                "elevenlabs_voice_id": account_voice_id,
                "shared_voice_id": body.voice_id,
                "public_owner_id": body.public_owner_id,
                "name": body.name,
                "description": body.description or "",
                "gender": body.gender or "",
                "accent": body.accent or "",
                "category": body.category or "",
                "language": body.language or "",
                "age": body.age or "",
                "preview_url": body.preview_url,
            },
        )
    except DuplicateVoiceError as exc:
        raise ConflictError(
            "Already in Voice Box, or nickname is taken"
        ) from exc
    return await _sign_box_row(storage, row)


@router.post(
    "/box/clone",
    response_model=UserVoiceOut,
    status_code=status.HTTP_201_CREATED,
)
async def clone_into_voice_box(
    user: CurrentUser,
    repo: Repo,
    storage: Storage,
    nickname: str = Form(...),
    language: str = Form(...),
    gender: str = Form(...),
    file: UploadFile = File(...),
) -> UserVoiceOut:
    """Instant Voice Clone from uploaded/recorded audio/video (≤3 min; longer → first 3)."""
    settings = get_settings()
    filename = (file.filename or "sample.bin").strip() or "sample.bin"
    suffix = Path(filename).suffix.lower() or ".bin"
    if suffix not in {
        ".mp4",
        ".mov",
        ".m4v",
        ".webm",
        ".mkv",
        ".mp3",
        ".m4a",
        ".wav",
        ".aac",
        ".ogg",
        ".flac",
    }:
        raise BadRequestError("지원하는 영상/오디오 파일만 업로드할 수 있습니다.")

    with tempfile.NamedTemporaryFile(prefix="dubby-ivc-up-", suffix=suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)
        total = 0
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > CLONE_MAX_UPLOAD_BYTES:
                tmp_path.unlink(missing_ok=True)
                raise BadRequestError("파일이 너무 큽니다 (최대 500MB).")
            tmp.write(chunk)

    try:
        fields = await clone_voice_into_box(
            settings=settings,
            storage=storage,
            owner_id=user.id,
            nickname=nickname,
            language=language,
            gender=gender,
            upload_path=tmp_path,
            upload_size=total,
        )
    except PipelineError as exc:
        raise BadRequestError(exc.message or str(exc)) from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    try:
        row = await repo.add_user_voice(user.id, fields)
    except DuplicateVoiceError as exc:
        # Best-effort cleanup of the newly created ElevenLabs voice.
        try:
            client = ElevenLabsClient(settings)
            await client.delete_voice(str(fields["elevenlabs_voice_id"]))
            await client.aclose()
        except Exception:
            pass
        preview = str(fields.get("preview_url") or "")
        if preview.startswith("users/"):
            try:
                await storage.delete_object(preview)
            except Exception:
                pass
        raise ConflictError("별명이 이미 사용 중입니다.") from exc

    return await _sign_box_row(storage, row)


@router.delete("/box/{voice_row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_from_voice_box(
    voice_row_id: UUID, user: CurrentUser, repo: Repo, storage: Storage
) -> None:
    existing = await repo.get_user_voice(user.id, voice_row_id)
    if not existing:
        raise NotFoundError("Voice not found in Voice Box")

    deleted = await repo.delete_user_voice(user.id, voice_row_id)
    if not deleted:
        raise NotFoundError("Voice not found in Voice Box")

    if is_cloned_voice_row(existing):
        settings = get_settings()
        el_id = str(existing.get("elevenlabs_voice_id") or "").strip()
        if el_id and settings.elevenlabs_api_key:
            try:
                client = ElevenLabsClient(settings)
                await client.delete_voice(el_id)
                await client.aclose()
            except Exception:
                pass
        preview = str(existing.get("preview_url") or "").strip()
        key = preview if preview.startswith("users/") else voice_preview_key(user.id, voice_row_id)
        try:
            await storage.delete_object(key)
        except Exception:
            pass
