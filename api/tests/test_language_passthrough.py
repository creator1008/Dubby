"""Unit tests for V2 language passthrough heuristics."""

from __future__ import annotations

from app.worker.language_passthrough import should_passthrough


def test_korean_source_keeps_hangul() -> None:
    assert not should_passthrough("난 너희를 위해서 호의를 베푸는 거야", "ko")


def test_korean_source_passthrough_english() -> None:
    assert should_passthrough("Hello everyone welcome to the show", "ko")


def test_english_source_passthrough_hangul() -> None:
    assert should_passthrough("안녕하세요 반갑습니다 오늘도", "en")


def test_short_or_empty_is_conservative() -> None:
    # Too short to judge → not passthrough (except empty)
    assert should_passthrough("", "ko")
    assert not should_passthrough("OK", "en")
