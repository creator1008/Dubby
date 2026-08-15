"""Tests for Vietnamese↔Korean locale rules (places + triệu spoken form)."""

from __future__ import annotations

from app.worker.locale_rules import (
    apply_vi_ko_postprocess,
    korean_spoken_from_millions,
    translation_pair_rules,
)
from app.worker.openai_client import build_translation_messages


def test_korean_spoken_from_millions_620_trieu() -> None:
    assert korean_spoken_from_millions(620) == "6억 2천만"
    assert korean_spoken_from_millions(50) == "5천만"
    assert korean_spoken_from_millions(100) == "1억"


def test_da_lat_not_da_nang_in_postprocess() -> None:
    src = "Đất gần Đà Lạt diện tích 191m"
    bad = "다낭 근처 땅 면적 191미터"
    assert "달랏" in apply_vi_ko_postprocess(src, bad)
    assert "다낭" not in apply_vi_ko_postprocess(src, bad)


def test_trieu_rewrites_wrong_man_form() -> None:
    src = "Mức giá hiện tại đang là 620 triệu."
    bad = "현재 가격은 620만입니다."
    fixed = apply_vi_ko_postprocess(src, bad)
    assert "6억 2천만" in fixed
    assert "620만" not in fixed


def test_vi_ko_translation_prompt_includes_glossary() -> None:
    messages = build_translation_messages(
        [(0, "Đà Lạt 620 triệu", 3.0)],
        "vi",
        "ko",
    )
    system = messages[0]["content"]
    assert "달랏" in system
    assert "다낭" in system
    assert "620 triệu" in system or "6억 2천만" in system
    assert translation_pair_rules("vi", "ko")
