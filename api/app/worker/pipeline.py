"""Pipeline job handlers (transcribe / dub).

Each handler receives a :class:`JobContext` and reports progress through
``ctx.report`` — the API and UI consume those ``message`` codes (see
``src/lib/job-labels.ts``).

Orchestration concerns owned here:

- per-job scratch directory with guaranteed cleanup,
- progress heartbeats (also while blocked on long subprocesses),
- cancellation checkpoints (job row leaving ``running`` stops the work),
- bounded retries for transient (network/API) failures,
- project status transitions and stable error codes.

The actual tool/service calls live behind :class:`app.worker.engine.Engine`;
``PIPELINE_MODE=mock`` swaps in the offline mock engine (dev/tests only).
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Awaitable, Callable, TypeVar
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError

from ..config import Settings
from ..db.base import Repository, Row
from ..storage import R2Storage
from . import errors
from .engine import create_engine
from .diarization import (
    assign_speakers,
    create_diarization_provider,
    split_speaker_turns,
)
from .errors import JobCancelled, PipelineError
from .lipsync import create_lipsync_provider
from .media import validate_source
from .openai_client import SegmentDraft
from .subtitles import build_ass
from .timing import choose_fit_policy, safe_slot_seconds, slot_seconds

logger = logging.getLogger("dubby.worker.pipeline")

T = TypeVar("T")


@dataclass
class JobContext:
    job_id: UUID
    project_id: UUID
    repo: Repository
    storage: R2Storage
    settings: Settings
    _last_progress: float = field(default=0.0, repr=False)
    _last_message: str = field(default="queued", repr=False)

    async def report(self, progress: float, message: str) -> None:
        """Publish progress (0..1) with a message code from job-labels.

        Doubles as a cancellation checkpoint: raises :class:`JobCancelled`
        when the job row is no longer ``running``.
        """
        await self.check_cancelled()
        self._last_progress = min(max(progress, 0.0), 1.0)
        self._last_message = message
        await self.repo.update_job_progress(
            self.job_id, progress=self._last_progress, message=message
        )

    async def heartbeat(self) -> None:
        """Refresh ``heartbeat_at`` mid-step so the reaper leaves us alone."""
        await self.check_cancelled()
        await self.repo.update_job_progress(
            self.job_id, progress=self._last_progress, message=self._last_message
        )

    async def check_cancelled(self) -> None:
        status = await self.repo.get_job_status(self.job_id)
        if status != "running":
            raise JobCancelled(f"job left running state (status={status!r})")


async def _with_retries(
    ctx: JobContext, fn: Callable[[], Awaitable[T]], *, step: str
) -> T:
    """Retry ``fn`` on retryable :class:`PipelineError` with linear backoff."""
    attempts = ctx.settings.pipeline_step_retries + 1
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except PipelineError as exc:
            if not exc.retryable or attempt >= attempts:
                raise
            delay = ctx.settings.pipeline_retry_backoff_seconds * attempt
            logger.warning(
                "step %s attempt %d/%d failed (%s); retrying in %.1fs",
                step, attempt, attempts, exc.code, delay,
            )
            await asyncio.sleep(delay)
            await ctx.check_cancelled()
    raise AssertionError("unreachable")


# --- scratch / storage helpers ---------------------------------------------------


def _make_scratch(ctx: JobContext) -> Path:
    parent = ctx.settings.scratch_dir or tempfile.gettempdir()
    Path(parent).mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"dubby-job-{ctx.job_id}-", dir=parent))


def _cleanup_scratch(scratch: Path) -> None:
    """Best-effort recursive removal; retried once for Windows file locks."""
    for _ in range(2):
        shutil.rmtree(scratch, ignore_errors=True)
        if not scratch.exists():
            return
        time.sleep(0.5)
    if scratch.exists():
        logger.warning("scratch %s could not be fully removed", scratch)


async def _download_source(ctx: JobContext, source_key: str, dest: Path) -> int:
    """Download the project source from R2; returns its size in bytes."""

    async def _head() -> int:
        try:
            head = await ctx.storage.head_object(source_key)
        except (ClientError, BotoCoreError) as exc:
            raise PipelineError(
                errors.SOURCE_DOWNLOAD_FAILED, f"R2 head failed: {exc}", retryable=True
            ) from exc
        if head is None:
            raise PipelineError(
                errors.SOURCE_MISSING, f"object {source_key!r} not found in R2"
            )
        return int(head.get("ContentLength", 0))

    size = await _with_retries(ctx, _head, step="head_source")
    if size > ctx.settings.max_source_bytes:
        raise PipelineError(
            errors.SOURCE_TOO_LARGE,
            f"source is {size} bytes; limit is {ctx.settings.max_source_bytes}",
        )

    async def _download() -> None:
        try:
            await ctx.storage.download_file(source_key, str(dest))
        except (ClientError, BotoCoreError) as exc:
            raise PipelineError(
                errors.SOURCE_DOWNLOAD_FAILED,
                f"R2 download failed: {exc}",
                retryable=True,
            ) from exc

    await _with_retries(ctx, _download, step="download_source")
    return size or dest.stat().st_size


async def _upload_output(
    ctx: JobContext, local_path: Path, key: str, content_type: str
) -> None:
    async def _upload() -> None:
        try:
            await ctx.storage.upload_file(str(local_path), key, content_type)
        except (ClientError, BotoCoreError) as exc:
            raise PipelineError(
                errors.UPLOAD_FAILED, f"R2 upload failed: {exc}", retryable=True
            ) from exc

    await _with_retries(ctx, _upload, step="upload_output")


async def _load_project(ctx: JobContext) -> Row:
    project = await ctx.repo.get_project_for_worker(ctx.project_id)
    if project is None:
        raise PipelineError(errors.INTERNAL, "project row disappeared")
    if not project.get("source_key"):
        raise PipelineError(
            errors.SOURCE_MISSING, "project has no uploaded source video"
        )
    return project


async def _set_project(ctx: JobContext, **fields: object) -> None:
    await ctx.repo.update_project_for_worker(ctx.project_id, dict(fields))


# --- handlers --------------------------------------------------------------------


async def run_transcribe(ctx: JobContext) -> None:
    """transcribe: R2 source -> validate -> ASR -> translate -> segments rows.

    Project: * -> processing -> ready_for_edit (failed on error).
    """
    engine = create_engine(ctx.settings, heartbeat=ctx.heartbeat)
    scratch = _make_scratch(ctx)
    revert_status = "uploaded"
    try:
        project = await _load_project(ctx)
        revert_status = str(project.get("status") or "uploaded")
        await _set_project(
            ctx,
            status="processing",
            output_key=None,
            lipsync_output_key=None,
            error=None,
        )
        await ctx.report(0.03, "measuring_duration")

        source_path = scratch / "source.bin"
        size = await _download_source(ctx, str(project["source_key"]), source_path)

        info = await engine.probe(str(source_path), size)
        validate_source(info, ctx.settings)
        await _set_project(ctx, duration_seconds=info.duration_seconds)

        await ctx.report(0.15, "extracting_audio")
        asr_audio = scratch / "asr_audio.mp3"
        await engine.extract_asr_audio(str(source_path), str(asr_audio))

        await ctx.report(0.35, "asr")
        drafts = await _with_retries(
            ctx,
            lambda: engine.transcribe(str(asr_audio), str(project["source_lang"])),
            step="asr",
        )
        if not drafts:
            raise PipelineError(errors.NO_SEGMENTS, "ASR produced no segments")

        speaker_assignments: list[tuple[str | None, bool]] = [
            (None, False) for _ in drafts
        ]
        quality_warnings: list[str] = []
        if bool(project.get("diarization_enabled")):
            provider = create_diarization_provider(ctx.settings)
            if provider is None:
                quality_warnings.append("diarization_provider_unavailable_single_speaker_fallback")
            else:
                await ctx.report(0.55, "diarization")
                turns = await _with_retries(
                    ctx, lambda: provider.diarize(str(asr_audio)), step="diarization"
                )
                timed_turns = split_speaker_turns(
                    turns,
                    round(ctx.settings.speech_segment_max_seconds * 1000),
                )
                if timed_turns:
                    drafts = [
                        SegmentDraft(turn.start_ms, turn.end_ms, turn.text)
                        for turn in timed_turns
                    ]
                    speaker_assignments = [
                        (turn.speaker_id, False) for turn in timed_turns
                    ]
                else:
                    speaker_assignments = assign_speakers(
                        [(d.start_ms, d.end_ms) for d in drafts], turns
                    )
                if any(overlap for _, overlap in speaker_assignments):
                    quality_warnings.append("overlapping_speakers_use_default_voice")

        await ctx.report(0.65, "translate")
        items = [
            (i, d.text, slot_seconds(d.start_ms, d.end_ms))
            for i, d in enumerate(drafts)
        ]
        translations: dict[int, str] = {}
        batch = ctx.settings.translation_batch_size
        for offset in range(0, len(items), batch):
            chunk = items[offset : offset + batch]
            translations.update(
                await _with_retries(
                    ctx,
                    lambda c=chunk: engine.translate_batch(
                        c,
                        str(project["source_lang"]),
                        str(project["target_lang"]),
                    ),
                    step="translate",
                )
            )
            done = min(offset + batch, len(items))
            await ctx.report(0.65 + 0.25 * done / len(items), "translate")

        rows: list[Row] = [
            {
                "idx": i,
                "start_ms": d.start_ms,
                "end_ms": d.end_ms,
                "source_text": d.text,
                "target_text": translations.get(i, ""),
                "speaker_id": speaker_assignments[i][0],
                "speaker_overlap": speaker_assignments[i][1],
            }
            for i, d in enumerate(drafts)
        ]
        await ctx.report(0.95, "refine_timing")
        await ctx.repo.replace_segments(ctx.project_id, rows)
        await _set_project(
            ctx,
            status="ready_for_edit",
            error=None,
            quality_warnings=quality_warnings,
        )
        await ctx.report(1.0, "done")
    except JobCancelled:
        with_status = revert_status if revert_status != "processing" else "uploaded"
        await _try_set_project(ctx, status=with_status)
        raise
    except PipelineError as exc:
        await _try_set_project(ctx, status="failed", error=str(exc))
        raise
    except Exception as exc:
        await _try_set_project(
            ctx, status="failed", error=f"{errors.INTERNAL}: {exc}"
        )
        raise
    finally:
        _cleanup_scratch(scratch)


async def run_dub(ctx: JobContext) -> None:
    """dub: stems -> voice clone -> TTS -> fit/mix -> subtitles -> mux -> R2.

    Project: ready_for_edit -> dubbing -> completed (failed on error).
    """
    engine = create_engine(ctx.settings, heartbeat=ctx.heartbeat)
    scratch = _make_scratch(ctx)
    voice_ids: set[str] = set()
    try:
        project = await _load_project(ctx)
        segments = await ctx.repo.list_segments_for_worker(ctx.project_id)
        speakable = [s for s in segments if str(s.get("target_text", "")).strip()]
        if not speakable:
            raise PipelineError(
                errors.NO_SEGMENTS,
                "no translated segments to dub; run transcribe first",
            )
        await _set_project(ctx, status="dubbing", lipsync_output_key=None, error=None)
        await ctx.report(0.02, "queued")

        source_path = scratch / "source.bin"
        size = await _download_source(ctx, str(project["source_key"]), source_path)
        info = await engine.probe(str(source_path), size)
        validate_source(info, ctx.settings)

        await ctx.report(0.08, "extracting_audio")
        full_wav = scratch / "audio.wav"
        await engine.extract_audio(str(source_path), str(full_wav))

        await ctx.report(0.15, "stem_split")
        stems_dir = scratch / "stems"
        stems_dir.mkdir()
        vocals, no_vocals = await engine.split_stems(str(full_wav), str(stems_dir))
        speech_ranges = [
            (int(segment["start_ms"]), int(segment["end_ms"]))
            for segment in segments
            if str(segment.get("source_text", "")).strip()
            and int(segment["end_ms"]) > int(segment["start_ms"])
        ]
        if not speech_ranges:
            raise PipelineError(
                errors.NO_SEGMENTS,
                "no ASR-recognized language ranges available for voice removal",
            )
        selective_bed = scratch / "speech_removed.wav"
        await engine.remove_recognized_speech(
            str(full_wav),
            no_vocals,
            speech_ranges,
            str(selective_bed),
        )
        source_levels: dict[int, float] = {}
        for segment in speakable:
            idx = int(segment["idx"])
            source_levels[idx] = await engine.measure_segment_loudness(
                vocals,
                int(segment["start_ms"]),
                int(segment["end_ms"]),
            )
        loudness_reference = (
            median(source_levels.values()) if source_levels else -20.0
        )
        gain_by_idx = {
            idx: max(-8.0, min(6.0, level - loudness_reference))
            for idx, level in source_levels.items()
        }

        await ctx.report(0.40, "voice_clone_tts")
        default_voice = await _with_retries(
            ctx,
            lambda: engine.prepare_voice(
                vocals, str(scratch), f"dubby-{ctx.project_id}"
            ),
            step="voice_clone",
        )
        voice_ids.add(default_voice)
        speaker_voices: dict[str, str] = {}
        speakers = sorted(
            {
                str(s["speaker_id"])
                for s in speakable
                if s.get("speaker_id") and not s.get("speaker_overlap")
            }
        )
        for speaker in speakers:
            ranges: list[tuple[int, int]] = []
            remaining_ms = int(ctx.settings.speaker_sample_seconds * 1000)
            for segment in speakable:
                if (
                    segment.get("speaker_id") != speaker
                    or segment.get("speaker_overlap")
                    or remaining_ms <= 0
                ):
                    continue
                start = int(segment["start_ms"])
                end = min(int(segment["end_ms"]), start + remaining_ms)
                if end > start:
                    ranges.append((start, end))
                    remaining_ms -= end - start
            speaker_voice = await _with_retries(
                ctx,
                lambda sp=speaker, rs=ranges: engine.prepare_voice(
                    vocals, str(scratch), f"dubby-{ctx.project_id}-{sp}", rs
                ),
                step="voice_clone",
            )
            speaker_voices[speaker] = speaker_voice
            voice_ids.add(speaker_voice)

        placed_clips: list[tuple[str, int]] = []
        quality_warnings = list(project.get("quality_warnings") or [])
        clips_dir = scratch / "clips"
        clips_dir.mkdir()
        total = len(speakable)
        for n, seg in enumerate(speakable):
            await ctx.report(0.45 + 0.30 * n / total, "tts")
            raw = clips_dir / f"seg_{seg['idx']}.{engine.tts_extension}"
            text = str(seg["target_text"]).strip()
            speaker_id = str(seg.get("speaker_id") or "")
            voice_id = (
                speaker_voices.get(speaker_id, default_voice)
                if not seg.get("speaker_overlap")
                else default_voice
            )
            await _with_retries(
                ctx,
                lambda t=text, p=str(raw), v=voice_id: engine.tts(
                    t,
                    v,
                    p,
                    str(project.get("tone_style") or "neutral"),
                    str(project["target_lang"]),
                ),
                step="tts",
            )
            clip_s = await engine.clip_duration_seconds(str(raw))
            next_start = (
                int(speakable[n + 1]["start_ms"]) if n + 1 < total else None
            )
            slot_s = safe_slot_seconds(
                int(seg["start_ms"]), int(seg["end_ms"]), next_start
            )
            ratio = clip_s / slot_s if slot_s > 0 else 1.0
            tolerance = ctx.settings.translation_timing_tolerance
            if ratio > 1 + tolerance or ratio < 1 - tolerance:
                direction = "compress" if ratio > 1 else "expand"
                try:
                    text = await _with_retries(
                        ctx,
                        lambda t=text, d=direction: engine.adjust_translation(
                            t, str(project["target_lang"]), slot_s, d
                        ),
                        step="timing_rewrite",
                    )
                    await _with_retries(
                        ctx,
                        lambda t=text, p=str(raw), v=voice_id: engine.tts(
                            t,
                            v,
                            p,
                            str(project.get("tone_style") or "neutral"),
                            str(project["target_lang"]),
                        ),
                        step="tts",
                    )
                    clip_s = await engine.clip_duration_seconds(str(raw))
                except PipelineError:
                    quality_warnings.append(f"segment_{seg['idx']}:timing_rewrite_failed")
            decision = choose_fit_policy(
                clip_s,
                slot_s,
                min_tempo=ctx.settings.tts_min_tempo,
                atempo_max=ctx.settings.tts_atempo_max,
                max_speedup=ctx.settings.tts_max_speedup,
                rubberband_available=bool(ctx.settings.rubberband_path),
            )
            if decision.warning:
                quality_warnings.append(f"segment_{seg['idx']}:{decision.warning}")
            fitted = clips_dir / f"seg_{seg['idx']}_fit.wav"
            await engine.fit_clip(
                str(raw),
                str(fitted),
                decision.tempo,
                decision.backend,
                decision.output_seconds,
                gain_by_idx.get(int(seg["idx"]), 0.0),
            )
            placed_clips.append((str(fitted), int(seg["start_ms"])))

        await ctx.report(0.78, "mix_bgm")
        mixed_wav = scratch / "mixed.wav"
        await engine.mix(str(selective_bed), placed_clips, str(mixed_wav))

        ass_path: str | None = None
        subtitle_mode = str(project.get("subtitle_mode") or "none")
        ass_text = build_ass(segments, subtitle_mode)  # type: ignore[arg-type]
        if ass_text is not None:
            await ctx.report(0.85, "burn_subtitles")
            ass_file = scratch / "subtitles.ass"
            ass_file.write_text(ass_text, encoding="utf-8")
            ass_path = str(ass_file)

        await ctx.report(0.88, "mux")
        output_path = scratch / "output.mp4"
        await engine.mux(str(source_path), str(mixed_wav), str(output_path), ass_path)

        await ctx.report(0.95, "mux")
        output_key = ctx.storage.output_key_for_source(
            str(project["source_key"]), f"dub_{project['target_lang']}.mp4"
        )
        await _upload_output(ctx, output_path, output_key, "video/mp4")

        await _set_project(
            ctx,
            status="completed",
            output_key=output_key,
            quality_warnings=sorted(set(quality_warnings)),
            error=None,
        )
        await ctx.report(1.0, "done")
    except JobCancelled:
        await _try_set_project(ctx, status="ready_for_edit")
        raise
    except PipelineError as exc:
        await _try_set_project(ctx, status="failed", error=str(exc))
        raise
    except Exception as exc:
        await _try_set_project(
            ctx, status="failed", error=f"{errors.INTERNAL}: {exc}"
        )
        raise
    finally:
        for voice_id in voice_ids:
            try:
                await engine.cleanup_voice(voice_id)
            except Exception:  # noqa: BLE001 - cleanup must not mask the job result
                logger.warning("could not clean up cloned voice %s", voice_id)
        _cleanup_scratch(scratch)


async def run_lipsync(ctx: JobContext) -> None:
    """Premium lip sync: provider polling -> validated result -> R2 output."""
    scratch = _make_scratch(ctx)
    try:
        project = await _load_project(ctx)
        output_key = str(project.get("output_key") or "")
        if not output_key:
            raise PipelineError(errors.FEATURE_UNAVAILABLE, "dub output is required")
        provider = create_lipsync_provider(ctx.settings)
        await ctx.report(0.05, "lipsync_submit")
        result_path = scratch / "lipsync.mp4"
        if ctx.settings.lipsync_provider == "mock":
            dubbed_path = scratch / "dubbed.mp4"
            await ctx.storage.download_file(output_key, str(dubbed_path))
            video_url = f"file://{dubbed_path}"
            audio_url = video_url
        else:
            video_url = await ctx.storage.presign_get(
                str(project["source_key"]), expires_in=ctx.settings.download_expires_seconds
            )
            audio_url = await ctx.storage.presign_get(
                output_key, expires_in=ctx.settings.download_expires_seconds
            )
        await provider.render(
            video_url,
            audio_url,
            str(result_path),
            idempotency_key=f"dubby-job-{ctx.job_id}",
        )
        await ctx.report(0.9, "lipsync_upload")
        result_key = ctx.storage.output_key_for_source(
            str(project["source_key"]), f"lipsync_{project['target_lang']}.mp4"
        )
        await _upload_output(ctx, result_path, result_key, "video/mp4")
        await _set_project(ctx, lipsync_output_key=result_key, error=None)
        await ctx.report(1.0, "done")
    except PipelineError as exc:
        await _try_set_project(ctx, error=str(exc))
        raise
    finally:
        _cleanup_scratch(scratch)


async def _try_set_project(ctx: JobContext, **fields: object) -> None:
    try:
        await ctx.repo.update_project_for_worker(ctx.project_id, dict(fields))
    except Exception:  # noqa: BLE001 - status writes must not mask the original error
        logger.exception("could not update project %s after job end", ctx.project_id)


PIPELINE_HANDLERS: dict[str, Callable[[JobContext], Awaitable[None]]] = {
    "transcribe": run_transcribe,
    "dub": run_dub,
    "lipsync": run_lipsync,
}
