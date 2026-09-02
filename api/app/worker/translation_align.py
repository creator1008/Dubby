"""Detect Gemini translation rows that do not belong to their source caption."""

from __future__ import annotations

import re

from .language_passthrough import dominant_script
from .locale_rules import spoken_char_budget

_HANGUL_RE = re.compile(r"[\uac00-\ud7af]")


def target_missing_expected_script(target_text: str, target_lang: str) -> bool:
    """True when a Korean (etc.) line has no target script — usually untranslated."""
    text = (target_text or "").strip()
    lang = (target_lang or "").strip().lower().split("-", 1)[0]
    if not text:
        return True
    if lang == "ko":
        return len(_HANGUL_RE.findall(text)) < 2
    if lang in {"zh", "ja"}:
        return dominant_script(text) not in {"cjk", None}
    return False


def translation_too_short_for_source(source_text: str, target_text: str) -> bool:
    """True when a long caption was collapsed to a stub (wrong idx assignment)."""
    source = (source_text or "").strip()
    target = (target_text or "").strip()
    if len(source) < 48:
        return False
    return len(target) < max(8, len(source) // 6)


def translation_too_long_for_slot(
    target_text: str,
    target_lang: str,
    slot_seconds: float,
) -> bool:
    """True when a short slot received a paragraph (document dump onto one idx)."""
    target = (target_text or "").strip()
    if not target:
        return False
    budget = spoken_char_budget(target_lang, max(0.35, float(slot_seconds)))
    return len(target) > max(int(budget * 2.2), 28)


def translation_expanded_from_short_source(source_text: str, target_text: str) -> bool:
    """True when a short caption received another scene's paragraph."""
    source = (source_text or "").strip()
    target = (target_text or "").strip()
    if len(source) > 24 or len(target) < 36:
        return False
    return len(target) > len(source) * 2


def idxs_needing_retranslate(
    items: list[tuple[int, str, float]],
    translated: dict[int, str],
    source_lang: str,
    target_lang: str,
) -> list[int]:
    """Return idxs whose first-pass translation is likely mis-assigned or untranslated."""
    del source_lang
    bad: list[int] = []
    for idx, source, seconds in items:
        target = str(translated.get(idx) or "").strip()
        if target_missing_expected_script(target, target_lang):
            bad.append(idx)
            continue
        if translation_too_short_for_source(source, target):
            bad.append(idx)
            continue
        if translation_expanded_from_short_source(source, target):
            bad.append(idx)
            continue
        if translation_too_long_for_slot(target, target_lang, seconds):
            bad.append(idx)
    return bad
