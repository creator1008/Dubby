"""Tests for Instant Voice Clone → My Voice Box helpers."""

from __future__ import annotations

import pytest

from app.errors import BadRequestError
from app.voice_clone import (
    CLONE_NICKNAME_STAR,
    is_cloned_voice_row,
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
    validate_clone_duration(60)
    validate_clone_duration(300)
    with pytest.raises(BadRequestError):
        validate_clone_duration(59.9)
    with pytest.raises(BadRequestError):
        validate_clone_duration(301)


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
