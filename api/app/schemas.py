"""Pydantic request/response models for the public REST API.

Field names mirror `src/lib/ui-types.ts` so the Next.js UI can consume
responses without a mapping layer.
"""

from __future__ import annotations

from datetime import datetime
import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

LangCode = Literal[
    "ko",
    "vi",
    "en",
    "zh",
    "ja",
    "es",
    "fr",
    "pt",
    "de",
    "ru",
    "ar",
    "ur",
    "id",
    "ms",
    "tr",
    "ta",
    "th",
    "my",
]
SubtitleMode = Literal["none", "source", "target"]
ToneStyle = Literal[
    "sad",
    "angry",
    "whisper",
    "excited",
    "energetic",
    "calm",
    "cheerful",
    # Legacy values still accepted from older clients / DB rows.
    "neutral",
    "warm",
    "serious",
]
VoiceMode = Literal["voice_box", "auto_clone"]
ProjectStatus = Literal[
    "created",
    "uploading",
    "uploaded",
    "processing",
    "ready_for_edit",
    "dubbing",
    "completed",
    "failed",
]
JobKind = Literal["transcribe", "dub", "lipsync"]
JobStatus = Literal["queued", "running", "completed", "failed", "cancelled"]


# --- Projects ---------------------------------------------------------------


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    source_lang: LangCode = "ko"
    target_lang: LangCode = "en"
    subtitle_mode: SubtitleMode = "none"
    tone_style: ToneStyle = "calm"
    diarization_enabled: bool = False
    voice_mode: VoiceMode = "voice_box"
    dub_voice_ids: list[str] = Field(default_factory=list, max_length=8)
    pipeline_version: str = "3.0"


class ProjectUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    source_lang: LangCode | None = None
    target_lang: LangCode | None = None
    subtitle_mode: SubtitleMode | None = None
    tone_style: ToneStyle | None = None
    diarization_enabled: bool | None = None
    voice_mode: VoiceMode | None = None
    dub_voice_ids: list[str] | None = Field(default=None, max_length=8)


class ProjectOut(BaseModel):
    id: UUID
    title: str
    status: str
    source_lang: str
    target_lang: str
    subtitle_mode: str
    tone_style: str = "calm"
    diarization_enabled: bool = False
    voice_mode: VoiceMode = "voice_box"
    dub_voice_ids: list[str] = Field(default_factory=list)
    pipeline_version: str = "3.0"
    duration_seconds: float | None = None
    source_key: str | None = None
    output_key: str | None = None
    lipsync_output_key: str | None = None
    quality_warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("voice_mode", mode="before")
    @classmethod
    def coerce_voice_mode(cls, value: object) -> object:
        if value in ("voice_box", "auto_clone"):
            return value
        return "voice_box"

    @field_validator("quality_warnings", mode="before")
    @classmethod
    def parse_quality_warnings(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return [value]
        return value

    @field_validator("dub_voice_ids", mode="before")
    @classmethod
    def parse_dub_voice_ids(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return []
        return value


# --- Segments ---------------------------------------------------------------


class SegmentUpdate(BaseModel):
    id: UUID
    target_text: str = Field(max_length=2000)
    source_text: str | None = Field(default=None, max_length=2000)
    end_ms: int | None = Field(default=None, ge=0)
    speak_speed: float | None = Field(default=None, ge=0.5, le=1.5)
    source_end_ms: int | None = Field(default=None, ge=0)
    emotion_tone: str | None = Field(
        default=None,
        pattern="^(sad|angry|whisper|excited|energetic|calm|cheerful)$",
    )


class SegmentsBulkUpdate(BaseModel):
    segments: list[SegmentUpdate] = Field(min_length=1, max_length=500)


class SegmentRetranslateItem(BaseModel):
    id: UUID
    source_text: str = Field(min_length=1, max_length=2000)


class SegmentsRetranslateRequest(BaseModel):
    segments: list[SegmentRetranslateItem] = Field(min_length=1, max_length=500)


class SegmentsRefreshPreviewRequest(BaseModel):
    """Regenerate dubbed preview clips for edited translations."""

    segment_ids: list[UUID] | None = Field(default=None, max_length=500)


class SegmentOut(BaseModel):
    id: UUID
    project_id: UUID
    idx: int
    start_ms: int
    end_ms: int
    source_text: str
    target_text: str
    speaker_id: str | None = None
    speaker_overlap: bool = False
    # Presigned URL for the dubbed TTS clip (subtitle editor preview).
    dubbed_audio_url: str | None = None
    speak_speed: float | None = None
    baseline_speak_speed: float | None = None
    # Speak rate used when the current preview clip was synthesized.
    clip_speak_speed: float | None = None
    # Original ASR end; may differ from end_ms after dub slot extension.
    source_end_ms: int | None = None
    # Detected / applied emotion tone for this segment's dub.
    emotion_tone: str | None = None


# --- Jobs -------------------------------------------------------------------


class JobCreate(BaseModel):
    kind: JobKind


class JobOut(BaseModel):
    id: UUID
    project_id: UUID
    kind: str
    status: str
    progress: float = 0
    message: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


# --- Credits ----------------------------------------------------------------


class CreditEntryOut(BaseModel):
    id: UUID
    delta_minutes: float
    reason: str
    project_id: UUID | None = None
    created_at: datetime


class CreditsOut(BaseModel):
    balance_minutes: float
    entries: list[CreditEntryOut]


# --- Voices ------------------------------------------------------------------


class SharedVoiceOut(BaseModel):
    public_owner_id: str
    voice_id: str
    name: str
    description: str | None = None
    gender: str = ""
    accent: str = ""
    category: str = ""
    language: str | None = None
    age: str = ""
    preview_url: str | None = None


class SharedVoicesOut(BaseModel):
    voices: list[SharedVoiceOut]
    has_more: bool = False
    total_count: int = 0
    page: int = 0


class VoiceFilterOptionsOut(BaseModel):
    languages: list[str]
    accents_by_language: dict[str, list[str]]
    genders: list[str]
    ages: list[str]
    categories: list[str]


class VoiceBoxCloneUploadCreate(BaseModel):
    filename: str = Field(min_length=1, max_length=300)
    content_type: str = Field(default="application/octet-stream", max_length=100)
    size_bytes: int = Field(gt=0)


class VoiceBoxCloneRequest(BaseModel):
    nickname: str = Field(min_length=1, max_length=30)
    language: str = Field(min_length=1, max_length=16)
    gender: str = Field(min_length=1, max_length=40)
    source_key: str = Field(min_length=1, max_length=500)


class UserVoiceCreate(BaseModel):
    voice_id: str = Field(min_length=1, max_length=120)
    public_owner_id: str = Field(min_length=1, max_length=200)
    nickname: str = Field(min_length=1, max_length=30)
    name: str = Field(default="", max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    gender: str = Field(default="", max_length=40)
    accent: str = Field(default="", max_length=80)
    category: str = Field(default="", max_length=80)
    language: str = Field(default="", max_length=16)
    age: str = Field(default="", max_length=40)
    preview_url: str | None = Field(default=None, max_length=2000)


class UserVoiceOut(BaseModel):
    id: UUID
    nickname: str
    elevenlabs_voice_id: str
    shared_voice_id: str
    public_owner_id: str = ""
    name: str = ""
    description: str = ""
    gender: str = ""
    accent: str = ""
    category: str = ""
    language: str = ""
    age: str = ""
    preview_url: str | None = None
    created_at: datetime


CheckoutKind = Literal["subscription", "credits"]


class CheckoutCreate(BaseModel):
    kind: CheckoutKind


class CheckoutOut(BaseModel):
    url: str


# --- Uploads (R2 multipart presign) ------------------------------------------


class MultipartCreateRequest(BaseModel):
    project_id: UUID
    filename: str = Field(min_length=1, max_length=300)
    content_type: str = Field(default="video/mp4", max_length=100)
    size_bytes: int = Field(gt=0)


class MultipartCreateResponse(BaseModel):
    upload_id: str
    key: str
    part_size_bytes: int
    part_count: int


class MultipartSignPartRequest(BaseModel):
    key: str
    part_number: int = Field(ge=1, le=10_000)


class MultipartSignPartResponse(BaseModel):
    url: str
    part_number: int
    expires_in: int


class CompletedPart(BaseModel):
    part_number: int = Field(ge=1, le=10_000)
    etag: str


class MultipartCompleteRequest(BaseModel):
    key: str
    parts: list[CompletedPart] = Field(min_length=1)


class MultipartCompleteResponse(BaseModel):
    key: str
    location: str | None = None


class MultipartAbortRequest(BaseModel):
    key: str


class DownloadUrlResponse(BaseModel):
    url: str
    expires_in: int


class SourceFromUrlRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


# --- Misc --------------------------------------------------------------------


class HealthOut(BaseModel):
    status: Literal["ok"]
    env: str
    version: str


class ReadyOut(BaseModel):
    status: Literal["ready", "degraded"]
    database: bool
