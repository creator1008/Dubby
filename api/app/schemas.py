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

LangCode = Literal["ko", "en", "vi"]
SubtitleMode = Literal["none", "source", "target"]
ToneStyle = Literal["neutral", "warm", "energetic", "serious"]
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
    subtitle_mode: SubtitleMode = "target"
    tone_style: ToneStyle = "neutral"
    diarization_enabled: bool = False


class ProjectUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    source_lang: LangCode | None = None
    target_lang: LangCode | None = None
    subtitle_mode: SubtitleMode | None = None
    tone_style: ToneStyle | None = None
    diarization_enabled: bool | None = None


class ProjectImportUrlRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)


class ProjectOut(BaseModel):
    id: UUID
    title: str
    status: str
    source_lang: str
    target_lang: str
    subtitle_mode: str
    tone_style: str = "neutral"
    diarization_enabled: bool = False
    duration_seconds: float | None = None
    source_key: str | None = None
    output_key: str | None = None
    lipsync_output_key: str | None = None
    quality_warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("quality_warnings", mode="before")
    @classmethod
    def parse_quality_warnings(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return [value]
        return value


# --- Segments ---------------------------------------------------------------


class SegmentUpdate(BaseModel):
    id: UUID
    target_text: str = Field(max_length=2000)
    source_text: str | None = Field(default=None, max_length=2000)


class SegmentsBulkUpdate(BaseModel):
    segments: list[SegmentUpdate] = Field(min_length=1, max_length=500)


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


# --- Misc --------------------------------------------------------------------


class HealthOut(BaseModel):
    status: Literal["ok"]
    env: str
    version: str


class ReadyOut(BaseModel):
    status: Literal["ready", "degraded"]
    database: bool
