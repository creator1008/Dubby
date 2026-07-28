"""Shared dubbing language codes for API validation and ASR/TTS prompts."""

from __future__ import annotations

SUPPORTED_LANGUAGES: frozenset[str] = frozenset(
    {
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
        "ko",
        "vi",
    }
)

LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "zh": "Chinese (Mandarin)",
    "ja": "Japanese",
    "es": "Spanish",
    "fr": "French",
    "pt": "Portuguese",
    "de": "German",
    "ru": "Russian",
    "ar": "Arabic",
    "ur": "Urdu",
    "id": "Indonesian",
    "ms": "Malay",
    "tr": "Turkish",
    "ta": "Tamil",
    "ko": "Korean",
    "vi": "Vietnamese",
}

# Whisper verbose_json language field → ISO code we use.
LANGUAGE_ALIASES: dict[str, str] = {
    "english": "en",
    "chinese": "zh",
    "mandarin": "zh",
    "japanese": "ja",
    "spanish": "es",
    "french": "fr",
    "portuguese": "pt",
    "german": "de",
    "russian": "ru",
    "arabic": "ar",
    "urdu": "ur",
    "indonesian": "id",
    "malay": "ms",
    "turkish": "tr",
    "tamil": "ta",
    "korean": "ko",
    "vietnamese": "vi",
    **{code: code for code in SUPPORTED_LANGUAGES},
}

# ISO codes for FastAPI Query regex.
LANG_QUERY_PATTERN = "^(en|zh|ja|es|fr|pt|de|ru|ar|ur|id|ms|tr|ta|ko|vi)$"
