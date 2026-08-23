"""Detect when a transcript segment should keep the original audio (passthrough).

V2 mixes onto the original bed and only replaces speech that matches the
project ``source_lang``. Other spoken languages stay like BGM/SFX.
"""

from __future__ import annotations

import re

_HANGUL_RE = re.compile(r"[\uac00-\ud7af]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
_CYRILLIC_RE = re.compile(r"[\u0400-\u04ff]")
_ARABIC_RE = re.compile(r"[\u0600-\u06ff]")
_THAI_RE = re.compile(r"[\u0e00-\u0e7f]")
_MYANMAR_RE = re.compile(r"[\u1000-\u109f]")
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097f]")

# Minimum letters before we trust a script heuristic.
_MIN_LETTERS = 4


def _letter_counts(text: str) -> dict[str, int]:
    return {
        "hangul": len(_HANGUL_RE.findall(text)),
        "latin": len(_LATIN_RE.findall(text)),
        "cjk": len(_CJK_RE.findall(text)),
        "cyrillic": len(_CYRILLIC_RE.findall(text)),
        "arabic": len(_ARABIC_RE.findall(text)),
        "thai": len(_THAI_RE.findall(text)),
        "myanmar": len(_MYANMAR_RE.findall(text)),
        "devanagari": len(_DEVANAGARI_RE.findall(text)),
    }


def dominant_script(text: str) -> str | None:
    counts = _letter_counts(text)
    total = sum(counts.values())
    if total < _MIN_LETTERS:
        return None
    best = max(counts.items(), key=lambda item: item[1])
    if best[1] / total < 0.55:
        return None
    return best[0]


def expected_script(source_lang: str) -> str | None:
    lang = (source_lang or "").strip().lower().split("-", 1)[0]
    return {
        "ko": "hangul",
        "ja": "cjk",
        "zh": "cjk",
        "yue": "cjk",
        "cmn": "cjk",
        "en": "latin",
        "es": "latin",
        "fr": "latin",
        "de": "latin",
        "pt": "latin",
        "it": "latin",
        "id": "latin",
        "ms": "latin",
        "vi": "latin",  # often Latin script in ASR
        "tr": "latin",
        "ru": "cyrillic",
        "uk": "cyrillic",
        "ar": "arabic",
        "ur": "arabic",
        "fa": "arabic",
        "th": "thai",
        "my": "myanmar",
        "hi": "devanagari",
        "ta": "latin",  # ASR often romanizes; do not force passthrough
    }.get(lang)


def should_passthrough(source_text: str, source_lang: str) -> bool:
    """True when the segment is likely not the declared source language."""
    text = (source_text or "").strip()
    if not text:
        return True
    expected = expected_script(source_lang)
    if expected is None:
        return False
    observed = dominant_script(text)
    if observed is None:
        return False
    return observed != expected
