"""Per-segment ``emotion_tag`` JSON used by STT, editor, and Eleven v4 TTS.

Schema (stored English; intensity + volume live inside the object)::

    {
      "class": "Happy",          # one of EMOTION_CLASSES
      "intensity": "medium",     # low | medium | high
      "volume": "normal",        # whisper | soft | normal | loud | shout
      "custom": null,            # optional English delivery note
      "source": "auto"           # auto | user | default
    }

Legacy project/segment tones (cheerful, energetic, whisper, …) map onto this set.
User-entered UI-language text is translated to English before storage.
"""

from __future__ import annotations

from typing import Any, Literal

EmotionClass = Literal[
    "Happy",
    "Calm",
    "Confident",
    "Hopeful",
    "Sad",
    "Angry",
    "Excited",
    "Nervous",
    "Serious",
    "Playful",
    "Worried",
    "Embarrassed",
    "Lonely",
]
EmotionIntensity = Literal["low", "medium", "high"]
EmotionVolume = Literal["whisper", "soft", "normal", "loud", "shout"]
EmotionSource = Literal["auto", "user", "default"]

EMOTION_CLASSES: tuple[EmotionClass, ...] = (
    "Happy",
    "Calm",
    "Confident",
    "Hopeful",
    "Sad",
    "Angry",
    "Excited",
    "Nervous",
    "Serious",
    "Playful",
    "Worried",
    "Embarrassed",
    "Lonely",
)

INTENSITIES: tuple[EmotionIntensity, ...] = ("low", "medium", "high")
VOLUMES: tuple[EmotionVolume, ...] = ("whisper", "soft", "normal", "loud", "shout")

# Legacy / product aliases → canonical English class.
_CLASS_ALIASES: dict[str, EmotionClass] = {
    "happy": "Happy",
    "cheerful": "Happy",
    "happily": "Happy",
    "warm": "Happy",
    "calm": "Calm",
    "neutral": "Calm",
    "confident": "Confident",
    "hopeful": "Hopeful",
    "sad": "Sad",
    "angry": "Angry",
    "excited": "Excited",
    "energetic": "Excited",
    "nervous": "Nervous",
    "nervously": "Nervous",
    "serious": "Serious",
    "playful": "Playful",
    "playfully": "Playful",
    "worried": "Worried",
    "embarrassed": "Embarrassed",
    "lonely": "Lonely",
    "whisper": "Calm",
}

# UI-language free text → class (Korean / Vietnamese / English stems).
_CUSTOM_CLASS_HINTS: tuple[tuple[tuple[str, ...], EmotionClass], ...] = (
    (("기쁘", "행복", "명랑", "유쾌", "vui", "hạnh phúc", "happy", "cheerful"), "Happy"),
    (("차분", "평온", "침착", "bình tĩnh", "calm"), "Calm"),
    (("자신", "당당", "tự tin", "confident"), "Confident"),
    (("희망", "기대", "hy vọng", "hopeful"), "Hopeful"),
    (("슬프", "우울", "buồn", "sad"), "Sad"),
    (("화난", "화나", "분노", "짜증", "tức", "giận", "angry"), "Angry"),
    (("흥분", "신나", "활기", "hào hứng", "excited", "energetic"), "Excited"),
    (("긴장", "초조", "lo lắng", "nervous"), "Nervous"),
    (("진지", "심각", "nghiêm", "serious"), "Serious"),
    (("장난", "익살", "tinh nghịch", "playful"), "Playful"),
    (("걱정", "우려", "lo", "worried"), "Worried"),
    (("부끄", "창피", "xấu hổ", "embarrassed"), "Embarrassed"),
    (("외로", "쓸쓸", "cô đơn", "lonely"), "Lonely"),
)

# Official Eleven v4 inline audio tags (Prompting Eleven v4 / v3 help).
# Stacked like ``[quietly] [excited]`` — not SSML, not ``eleven_4``.
_CLASS_AUDIO_TAG: dict[EmotionClass, str] = {
    "Happy": "[happily]",
    "Calm": "[calm]",
    "Confident": "[confident]",
    "Hopeful": "[hopeful]",
    "Sad": "[sad]",
    "Angry": "[angry]",
    "Excited": "[excited]",
    "Nervous": "[nervously]",
    "Serious": "[serious]",
    "Playful": "[playfully]",
    "Worried": "[worried]",
    "Embarrassed": "[embarrassed]",
    "Lonely": "[lonely]",
}
_INTENSITY_AUDIO_TAG: dict[str, str] = {
    "low": "[softly]",
    "medium": "",
    "high": "[intensely]",
}
_VOLUME_AUDIO_TAG: dict[str, str] = {
    "whisper": "[whispers]",
    "soft": "[quietly]",
    "normal": "",
    "loud": "[loudly]",
    "shout": "[shouting]",
}

_CLASS_STABILITY: dict[EmotionClass, float] = {
    "Happy": 0.40,
    "Calm": 0.70,
    "Confident": 0.55,
    "Hopeful": 0.50,
    "Sad": 0.85,
    "Angry": 0.15,
    "Excited": 0.10,
    "Nervous": 0.35,
    "Serious": 0.65,
    "Playful": 0.30,
    "Worried": 0.45,
    "Embarrassed": 0.55,
    "Lonely": 0.80,
}


def default_emotion_tag(*, fallback: EmotionClass = "Calm") -> dict[str, Any]:
    return {
        "class": fallback,
        "intensity": "medium",
        "volume": "normal",
        "custom": None,
        "source": "default",
    }


def normalize_emotion_class(
    value: str | None, *, fallback: EmotionClass = "Calm"
) -> EmotionClass:
    raw = (value or "").strip()
    if not raw:
        return fallback
    if raw in EMOTION_CLASSES:
        return raw  # type: ignore[return-value]
    mapped = _CLASS_ALIASES.get(raw.lower())
    if mapped:
        return mapped
    hinted = class_from_custom_text(raw)
    return hinted or fallback


def class_from_custom_text(text: str) -> EmotionClass | None:
    lowered = (text or "").strip().lower()
    if not lowered:
        return None
    for needles, klass in _CUSTOM_CLASS_HINTS:
        if any(needle in lowered for needle in needles):
            return klass
    return None


def _normalize_intensity(value: object) -> EmotionIntensity:
    raw = str(value or "medium").strip().lower()
    if raw in {"low", "medium", "high"}:
        return raw  # type: ignore[return-value]
    if raw in {"soft", "quiet", "mild"}:
        return "low"
    if raw in {"strong", "intense", "loud"}:
        return "high"
    return "medium"


def _normalize_volume(value: object, *, klass: EmotionClass | None = None) -> EmotionVolume:
    raw = str(value or "normal").strip().lower()
    if raw in VOLUMES:
        return raw  # type: ignore[return-value]
    if raw in {"quiet"}:
        return "soft"
    if raw in {"shout", "shouting", "yell"}:
        return "shout"
    if raw in {"whisper", "whispers"}:
        return "whisper"
    if klass == "Angry" and raw == "high":
        return "loud"
    return "normal"


def parse_emotion_tag(
    value: object | None,
    *,
    fallback: EmotionClass = "Calm",
    source: EmotionSource | None = None,
) -> dict[str, Any]:
    """Coerce DB/manifest/editor payloads into the canonical object."""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            tag = default_emotion_tag(fallback=fallback)
        else:
            try:
                import json

                parsed = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                parsed = None
            if isinstance(parsed, dict):
                return parse_emotion_tag(parsed, fallback=fallback, source=source)
            klass = normalize_emotion_class(text, fallback=fallback)
            custom = None if text.lower() in _CLASS_ALIASES or text in EMOTION_CLASSES else text
            tag = {
                "class": klass,
                "intensity": "medium",
                "volume": "whisper" if text.strip().lower() == "whisper" else "normal",
                "custom": custom,
                "source": source or ("user" if custom else "auto"),
            }
            return tag
    if not isinstance(value, dict):
        tag = default_emotion_tag(fallback=fallback)
        if source:
            tag["source"] = source
        return tag
    klass = normalize_emotion_class(
        str(value.get("class") or value.get("emotion") or value.get("tone") or ""),
        fallback=fallback,
    )
    custom_raw = value.get("custom")
    custom = str(custom_raw).strip() if custom_raw else None
    tag = {
        "class": klass,
        "intensity": _normalize_intensity(value.get("intensity")),
        "volume": _normalize_volume(value.get("volume"), klass=klass),
        "custom": custom or None,
        "source": str(value.get("source") or source or "auto"),
    }
    if tag["source"] not in {"auto", "user", "default"}:
        tag["source"] = "auto"
    return tag


def emotion_tag_from_tone(
    tone: str | None,
    *,
    fallback: EmotionClass = "Calm",
    source: EmotionSource = "auto",
) -> dict[str, Any]:
    raw = (tone or "").strip()
    volume: EmotionVolume = "whisper" if raw.lower() == "whisper" else "normal"
    return {
        "class": normalize_emotion_class(raw, fallback=fallback),
        "intensity": "medium",
        "volume": volume,
        "custom": None,
        "source": source,
    }


def tone_from_emotion_tag(tag: object | None) -> str:
    """Legacy string used by older TTS / preview paths."""
    parsed = parse_emotion_tag(tag)
    if parsed.get("volume") == "whisper":
        return "whisper"
    return str(parsed["class"]).lower()


def normalize_emotion_mode(value: object | None, *, fallback: str = "auto") -> str:
    raw = str(value or fallback).strip().lower()
    return "custom" if raw == "custom" else "auto"


def resolve_tts_emotion_tag(
    *,
    mode: str | None,
    user_tag: object | None = None,
    user_tone: str | None = None,
    detected_tag: object | None = None,
    detected_tone: str | None = None,
    pcm_tag: object | None = None,
    project_tag: object | None = None,
    project_tone: str | None = None,
) -> dict[str, Any]:
    """Pick the Eleven v4 tag from 사용자 지정 vs 자동감지 — never editor-always-wins.

    custom → user-registered tag (segment override, else project default).
    auto → AI-extracted tag; user overrides are ignored.
    """
    fallback = normalize_emotion_class(project_tone)
    if normalize_emotion_mode(mode) == "custom":
        if isinstance(user_tag, dict):
            return parse_emotion_tag(user_tag, fallback=fallback, source="user")
        if (user_tone or "").strip():
            return emotion_tag_from_tone(
                user_tone, fallback=fallback, source="user"
            )
        if isinstance(project_tag, dict):
            return parse_emotion_tag(project_tag, fallback=fallback, source="user")
        return emotion_tag_from_tone(
            project_tone, fallback=fallback, source="user"
        )
    if isinstance(detected_tag, dict):
        return parse_emotion_tag(detected_tag, fallback=fallback, source="auto")
    if isinstance(pcm_tag, dict):
        return parse_emotion_tag(pcm_tag, fallback=fallback, source="auto")
    if (detected_tone or "").strip():
        return emotion_tag_from_tone(
            detected_tone, fallback=fallback, source="auto"
        )
    return default_emotion_tag(fallback=fallback)


def apply_user_emotion_input(
    raw: str,
    *,
    english_custom: str | None = None,
    fallback: EmotionClass = "Calm",
    intensity: str | None = None,
    volume: str | None = None,
) -> dict[str, Any]:
    """Editor class pick or UI-language free text → stored English tag."""
    text = (raw or "").strip()
    custom = (english_custom or "").strip() or None
    if text in EMOTION_CLASSES or text.lower() in _CLASS_ALIASES:
        tag = emotion_tag_from_tone(text, fallback=fallback, source="user")
    else:
        klass = class_from_custom_text(custom or text) or fallback
        tag = {
            "class": klass,
            "intensity": "medium",
            "volume": "normal",
            "custom": custom or text or None,
            "source": "user",
        }
    if intensity:
        tag["intensity"] = _normalize_intensity(intensity)
    if volume:
        tag["volume"] = _normalize_volume(volume, klass=tag["class"])
    return tag


def audio_tags_from_emotion_tag(tag: object | None) -> str:
    """Documented v4 inline tags, e.g. ``[quietly] [excited] ``."""
    parsed = parse_emotion_tag(tag)
    parts: list[str] = []
    volume = _VOLUME_AUDIO_TAG.get(str(parsed.get("volume") or ""), "")
    intensity = _INTENSITY_AUDIO_TAG.get(str(parsed.get("intensity") or ""), "")
    klass = _CLASS_AUDIO_TAG.get(parsed["class"], "")
    if volume:
        parts.append(volume)
    if intensity:
        parts.append(intensity)
    if klass:
        parts.append(klass)
    custom = str(parsed.get("custom") or "").strip()
    if custom:
        cleaned = custom.strip("[]")
        if cleaned and f"[{cleaned.lower()}]" not in {p.lower() for p in parts}:
            parts.append(f"[{cleaned}]")
    if not parts:
        return ""
    return " ".join(parts) + " "


def stability_for_emotion_tag(tag: object | None) -> float:
    parsed = parse_emotion_tag(tag)
    base = _CLASS_STABILITY.get(parsed["class"], 0.5)
    if parsed["intensity"] == "high":
        base = max(0.0, base - 0.15)
    elif parsed["intensity"] == "low":
        base = min(1.0, base + 0.15)
    if parsed["volume"] == "whisper":
        base = max(base, 0.85)
    elif parsed["volume"] in {"loud", "shout"}:
        base = min(base, 0.25)
    return round(base, 2)


def features_to_emotion_tag(
    features: dict[str, float],
    *,
    source_text: str = "",
    fallback: EmotionClass = "Calm",
) -> dict[str, Any]:
    """Acoustic + light text cues → auto emotion_tag (no extra vendor hop)."""
    loud = float(features.get("loudness_db") or -60.0)
    dynamics = float(features.get("dynamics") or 0.0)
    zcr = float(features.get("zcr") or 0.0)
    f0 = float(features.get("f0_hz") or 0.0)
    f0_std = float(features.get("f0_std") or 0.0)
    duration_ms = float(features.get("duration_ms") or 0.0)
    text = source_text or ""
    text_excited = "!" in text or "！" in text

    if duration_ms < 180 or loud <= -55:
        return default_emotion_tag(fallback=fallback)

    klass: EmotionClass = fallback
    intensity: EmotionIntensity = "medium"
    volume: EmotionVolume = "normal"

    if loud <= -38 and zcr >= 0.08:
        klass, volume, intensity = "Calm", "whisper", "low"
    elif loud <= -42 and dynamics < 3.5:
        klass, volume = "Calm", "whisper"
    elif loud >= -16 and dynamics >= 3.8:
        klass, volume, intensity = "Angry", "loud", "high"
    elif loud >= -18 and dynamics >= 4.2 and f0_std >= 18:
        klass, intensity = "Angry", "high"
    elif loud >= -18 and f0_std >= 28:
        klass, intensity = "Excited", "high"
    elif loud >= -20 and f0_std >= 22 and (f0 >= 160 or text_excited):
        klass = "Excited"
    elif loud >= -20 and text_excited:
        klass = "Excited"
    elif f0 >= 175 and loud >= -28 and f0_std >= 10:
        klass = "Happy"
    elif text_excited and loud >= -30 and f0 >= 150:
        klass = "Happy"
    elif loud <= -30 and f0 > 0 and f0 <= 145 and f0_std <= 12:
        klass, intensity = "Sad", "low"
    elif loud <= -32 and f0_std <= 8:
        klass = "Sad"
    elif loud <= -24 and f0_std <= 14:
        klass = "Calm"
    else:
        klass = fallback

    if loud >= -14:
        volume = "shout" if klass == "Angry" else "loud"
        intensity = "high"
    elif loud <= -40 and volume != "whisper":
        volume = "soft"
        intensity = "low"

    return {
        "class": klass,
        "intensity": intensity,
        "volume": volume,
        "custom": None,
        "source": "auto",
    }


def detect_emotion_tags_for_segments(
    wav_path: str,
    segments: list[dict[str, Any]],
    *,
    fallback: EmotionClass = "Calm",
) -> dict[int, dict[str, Any]]:
    """PCM analysis → emotion_tag per idx. Missing/short clips use fallback."""
    from .emotion import extract_emotion_features

    out: dict[int, dict[str, Any]] = {}
    default = default_emotion_tag(fallback=fallback)
    for row in segments:
        try:
            idx = int(row["idx"])
            start_ms = int(row["start_ms"])
        except (KeyError, TypeError, ValueError):
            continue
        source_end = row.get("source_end_ms")
        try:
            end_ms = int(source_end) if source_end is not None else int(row["end_ms"])
        except (TypeError, ValueError, KeyError):
            continue
        if end_ms <= start_ms:
            out[idx] = dict(default)
            continue
        try:
            analysis_end = min(max(start_ms + 120, end_ms), start_ms + 1500)
            features = extract_emotion_features(wav_path, start_ms, analysis_end)
            out[idx] = features_to_emotion_tag(
                features,
                source_text=str(row.get("source_text") or row.get("text") or ""),
                fallback=fallback,
            )
        except Exception:
            out[idx] = dict(default)
    return out
