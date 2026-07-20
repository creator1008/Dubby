"""Pipeline error taxonomy.

Every failure surfaced to the jobs table carries a stable ``code`` so the
UI/support can distinguish user-fixable problems (bad upload) from transient
infrastructure issues (API hiccups, which the step-retry loop already
retried) without parsing prose.
"""

from __future__ import annotations


class PipelineError(Exception):
    """A pipeline step failed.

    ``retryable`` marks errors worth retrying within the same job run
    (network/API transients). Validation errors are never retryable.
    """

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable


class JobCancelled(Exception):
    """The job row left the ``running`` state (user cancellation / reaper).

    Raised from cancellation checkpoints; the runner must NOT overwrite the
    job status when it sees this.
    """


# Stable error codes (referenced by tests; keep values unchanged).
SOURCE_MISSING = "source_missing"
SOURCE_DOWNLOAD_FAILED = "source_download_failed"
SOURCE_TOO_LARGE = "source_too_large"
SOURCE_TOO_LONG = "source_too_long"
SOURCE_UNSUPPORTED_CONTAINER = "source_unsupported_container"
SOURCE_NO_AUDIO = "source_no_audio"
PROBE_FAILED = "probe_failed"
FFMPEG_FAILED = "ffmpeg_failed"
DEMUCS_FAILED = "demucs_failed"
ASR_FAILED = "asr_failed"
TRANSLATION_FAILED = "translation_failed"
NO_SEGMENTS = "no_segments"
VOICE_CLONE_FAILED = "voice_clone_failed"
TTS_FAILED = "tts_failed"
LIPSYNC_FAILED = "lipsync_failed"
FEATURE_UNAVAILABLE = "feature_unavailable"
UPLOAD_FAILED = "upload_failed"
CONFIG_MISSING = "config_missing"
INTERNAL = "internal_error"
