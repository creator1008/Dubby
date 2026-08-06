"""Voice Setting: ElevenLabs library browse + My Voice Box CRUD."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, status

from ..auth import CurrentUser
from ..config import get_settings
from ..db.base import DuplicateVoiceError
from ..deps import Repo
from ..errors import BadRequestError, ConflictError, NotFoundError
from ..schemas import (
    SharedVoiceOut,
    SharedVoicesOut,
    UserVoiceCreate,
    UserVoiceOut,
    VoiceFilterOptionsOut,
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
async def list_voice_box(user: CurrentUser, repo: Repo) -> list[UserVoiceOut]:
    rows = await repo.list_user_voices(user.id)
    return [UserVoiceOut.model_validate(r) for r in rows]


@router.post(
    "/box",
    response_model=UserVoiceOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_to_voice_box(
    body: UserVoiceCreate, user: CurrentUser, repo: Repo
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
    return UserVoiceOut.model_validate(row)


@router.delete("/box/{voice_row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_from_voice_box(
    voice_row_id: UUID, user: CurrentUser, repo: Repo
) -> None:
    deleted = await repo.delete_user_voice(user.id, voice_row_id)
    if not deleted:
        raise NotFoundError("Voice not found in Voice Box")
