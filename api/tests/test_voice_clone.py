"""Tests for Instant Voice Clone → My Voice Box helpers."""

from __future__ import annotations

import pytest

from app.errors import BadRequestError
from app.voice_clone import (
    CLONE_MAX_SECONDS,
    CLONE_NICKNAME_STAR,
    clone_sample_seconds,
    clone_upload_suffix,
    is_cloned_voice_row,
    is_voice_clone_inbox_key,
    starred_nickname,
    validate_clone_duration,
    voice_preview_key,
)
from uuid import UUID


def test_starred_nickname_prefixes_once() -> None:
    assert starred_nickname("달랏") == f"{CLONE_NICKNAME_STAR}달랏"
    assert starred_nickname(f"{CLONE_NICKNAME_STAR}달랏") == f"{CLONE_NICKNAME_STAR}달랏"
    assert len(starred_nickname("x" * 40)) <= 30


def test_validate_clone_duration_bounds() -> None:
    validate_clone_duration(1.0)
    validate_clone_duration(45.0)
    validate_clone_duration(60)
    validate_clone_duration(300)
    validate_clone_duration(600)
    with pytest.raises(BadRequestError):
        validate_clone_duration(0.5)


def test_clone_sample_seconds_caps_long_clips() -> None:
    assert clone_sample_seconds(30.0) == 30.0
    assert clone_sample_seconds(60.0) == 60.0
    assert clone_sample_seconds(180.0) == CLONE_MAX_SECONDS
    assert clone_sample_seconds(300.0) == CLONE_MAX_SECONDS
    assert clone_sample_seconds(601.0) == CLONE_MAX_SECONDS


def test_is_cloned_voice_row() -> None:
    assert is_cloned_voice_row({"shared_voice_id": "ivc:abc", "public_owner_id": ""})
    assert is_cloned_voice_row(
        {"shared_voice_id": "x", "public_owner_id": "dubby:ivc"}
    )
    assert not is_cloned_voice_row(
        {"shared_voice_id": "libvoice", "public_owner_id": "owner"}
    )


def test_voice_preview_key() -> None:
    uid = UUID("00000000-0000-0000-0000-000000000001")
    vid = UUID("00000000-0000-0000-0000-000000000002")
    assert voice_preview_key(uid, vid) == (
        "users/00000000-0000-0000-0000-000000000001/"
        "voices/00000000-0000-0000-0000-000000000002/preview.mp3"
    )


def test_is_voice_clone_inbox_key() -> None:
    uid = UUID("00000000-0000-0000-0000-000000000001")
    other = UUID("00000000-0000-0000-0000-000000000099")
    key = f"users/{uid}/voices/inbox/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/real1.mp4"
    assert is_voice_clone_inbox_key(key, uid)
    assert not is_voice_clone_inbox_key(key, other)
    assert not is_voice_clone_inbox_key(
        f"users/{uid}/projects/{uid}/source/real1.mp4", uid
    )
    assert not is_voice_clone_inbox_key(
        f"users/{uid}/voices/inbox/../projects/x.mp4", uid
    )
    assert clone_upload_suffix(key) == ".mp4"
    with pytest.raises(BadRequestError):
        clone_upload_suffix("notes.txt")
