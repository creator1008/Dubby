"""Locale-specific ASR / translation rules (glossary + spoken forms).

Prompt hints alone are unreliable for place names and Vietnamese currency, so
we also apply deterministic post-processing after VI→KO translation.
"""

from __future__ import annotations

import re

# --- Vietnamese → Korean place / currency helpers ------------------------------

_VI_TRIEU_RE = re.compile(
    r"(?<!\d)(\d{1,3}(?:[.,]\d+)?)\s*triệu\b",
    re.IGNORECASE,
)


def korean_spoken_from_millions(millions: float) -> str:
    """Convert N triệu (N million) into Korean 억/만 notation for TTS.

    Example: 620 → ``6억 2천만`` (620,000,000).
    """
    total = int(round(millions * 1_000_000))
    if total <= 0:
        return "0"
    eok, rem = divmod(total, 100_000_000)
    cheon_man, rem = divmod(rem, 10_000_000)
    man, ones = divmod(rem, 10_000)
    parts: list[str] = []
    if eok:
        parts.append(f"{eok}억")
    if cheon_man:
        # 2 → 2천만, 1 → 1천만
        parts.append(f"{cheon_man}천만")
    if man:
        parts.append(f"{man}만")
    if ones:
        parts.append(str(ones))
    return " ".join(parts) if parts else "0"


def translation_pair_rules(source_lang: str, target_lang: str) -> str:
    """Extra system-prompt rules for a language pair (may be empty)."""
    src = (source_lang or "").strip().lower()
    tgt = (target_lang or "").strip().lower()
    if src == "vi" and tgt == "ko":
        return (
            "Vietnamese→Korean glossary (mandatory):\n"
            "- Đà Lạt / Da Lat → 달랏 (highland city). NEVER translate it as 다낭.\n"
            "- Đà Nẵng / Da Nang → 다낭 (different coastal city).\n"
            "- Hồ Chí Minh / Sài Gòn → 호치민; Hà Nội → 하노이.\n"
            "Vietnamese currency spoken form (mandatory for voice-over):\n"
            "- N triệu = N million đồng. Render in Korean 억/만, not '만' alone "
            "and not a raw Latin number.\n"
            "- Example: 620 triệu → 6억 2천만 (spoken 육억 이천만).\n"
            "- Example: 1.2 tỷ → 12억; 50 triệu → 5천만.\n"
            "Keep real-estate terms natural: thổ cư→토지/주거용 토지, "
            "mặt tiền→도로 전면, view thung lũng→계곡 전망."
        )
    if src == "vi":
        return (
            "Preserve Vietnamese place names accurately: Đà Lạt is Da Lat "
            "(not Da Nang); Đà Nẵng is Da Nang. Spell currency for speech: "
            "N triệu = N million."
        )
    return ""


def asr_proofread_rules(language: str) -> str:
    """Extra ASR proofreading hints for a source language."""
    lang = (language or "").strip().lower()
    if lang == "vi":
        return (
            "Vietnamese ASR pitfalls: keep diacritics; do not swap Đà Lạt with "
            "Đà Nẵng; prefer real-estate terms thổ cư, mặt tiền, thung lũng, "
            "săn mây, triệu when the audio supports them; remove spurious "
            "inserted words like hết when neighbors do not support them."
        )
    if lang == "ko":
        return (
            "Korean example: if someone survives because caretakers fed them, "
            "prefer 살아지더라 over 사라지더라 when Whisper misheard survival "
            "as disappearance — choose the reading that makes sense with the "
            "surrounding story."
        )
    return ""


def whisper_vocab_prompt(language: str) -> str | None:
    """Optional Whisper ``prompt`` vocabulary hint (keep short to limit echo)."""
    lang = (language or "").strip().lower()
    if lang == "vi":
        return (
            "Đà Lạt, Đà Nẵng, thổ cư, mặt tiền, thung lũng, săn mây, "
            "hoàng hôn, triệu, tỷ."
        )
    return None


def _source_mentions(pattern: re.Pattern[str], source_text: str) -> bool:
    return bool(pattern.search(source_text or ""))


def apply_vi_ko_postprocess(source_text: str, korean_text: str) -> str:
    """Deterministic fixes after Vietnamese→Korean translation."""
    src = source_text or ""
    out = korean_text or ""
    if not out.strip():
        return out

    # Place names: if source clearly has Đà Lạt, kill erroneous 다낭.
    if _source_mentions(re.compile(r"đà\s*lạt|da\s*lat|dalat", re.I), src):
        # Only rewrite 다낭 when Đà Nẵng is not also in the source segment.
        if not _source_mentions(re.compile(r"đà\s*nẵng|da\s*nang|danang", re.I), src):
            out = out.replace("다낭", "달랏")
        # Normalize Latin leftovers.
        out = re.sub(r"Đà\s*Lạt|Da\s*Lat|Dalat", "달랏", out, flags=re.I)

    if _source_mentions(re.compile(r"đà\s*nẵng|da\s*nang|danang", re.I), src):
        out = re.sub(r"Đà\s*Nẵng|Da\s*Nang|Danang", "다낭", out, flags=re.I)

    # Currency: rewrite each N triệu mentioned in the source into Korean 억/만.
    for match in _VI_TRIEU_RE.finditer(src):
        raw = match.group(1).replace(",", ".")
        try:
            millions = float(raw)
        except ValueError:
            continue
        spoken = korean_spoken_from_millions(millions)
        n_int = int(millions) if millions == int(millions) else millions
        n_str = str(int(millions)) if millions == int(millions) else str(millions)
        # Common wrong / half-translated forms → correct spoken form.
        candidates = [
            rf"{re.escape(n_str)}\s*만(?:\s*동)?",
            rf"{re.escape(n_str)}\s*백만",
            rf"{re.escape(n_str)}\s*트리에우",
            rf"{re.escape(n_str)}\s*triệu",
            rf"{re.escape(n_str)}\s*million",
            rf"{re.escape(str(n_int))}\s*만(?:\s*동)?",
        ]
        replaced = False
        for cand in candidates:
            new_out, n = re.subn(cand, spoken, out, count=1, flags=re.IGNORECASE)
            if n:
                out = new_out
                replaced = True
                break
        if not replaced and spoken not in out and n_str in out:
            # Bare number left as "620" near price context → prefer spoken form.
            out = re.sub(
                rf"(?<!\d){re.escape(n_str)}(?!\d)(?=\s*(?:입니다|이에요|예요|원|동)?)",
                spoken,
                out,
                count=1,
            )

    return out


def apply_translation_postprocess(
    source_text: str,
    translated_text: str,
    source_lang: str,
    target_lang: str,
) -> str:
    src = (source_lang or "").strip().lower()
    tgt = (target_lang or "").strip().lower()
    if src == "vi" and tgt == "ko":
        return apply_vi_ko_postprocess(source_text, translated_text)
    return translated_text


def apply_translation_map_postprocess(
    source_by_idx: dict[int, str],
    translated_by_idx: dict[int, str],
    source_lang: str,
    target_lang: str,
) -> dict[int, str]:
    return {
        idx: apply_translation_postprocess(
            source_by_idx.get(idx, ""),
            text,
            source_lang,
            target_lang,
        )
        for idx, text in translated_by_idx.items()
    }
