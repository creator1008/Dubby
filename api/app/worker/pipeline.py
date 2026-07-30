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
import json
import logging
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, TypeVar
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError

from ..config import Settings
from ..db.base import Repository, Row
from ..storage import R2Storage
from . import errors
from .engine import create_engine
from .diarization import (
    SpeakerTurn,
    assign_speakers,
    create_diarization_provider,
)
from .dub_quality import (
    matched_loudness_gain,
    source_loudness_levels_async,
    voice_removal_ranges,
)
from .errors import JobCancelled, PipelineError
from .lipsync import create_lipsync_provider
from .media import ffmpeg_has_rubberband, validate_source
from .openai_client import SegmentDraft
from .subtitles import build_ass
from .timing import (
    choose_fit_policy,
    initial_speak_speed,
    safe_slot_seconds,
    slot_seconds,
    speak_speed_for_slot,
)
from .utterance_pipeline import (
    UtteranceChunk,
    align_document_translation,
    build_breath_utterances,
    dedupe_boundary_overlaps,
    merge_dangling_chunks,
    place_long_units_by_timestamps,
)

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


def _speech_ranges_key(ctx: JobContext, source_key: str) -> str:
    return ctx.storage.meta_key_for_source(source_key, "speech_ranges.json")


def _voice_removed_key(ctx: JobContext, source_key: str) -> str:
    return ctx.storage.meta_key_for_source(source_key, "voice_removed.mp4")


async def _build_and_upload_voice_removed(
    ctx: JobContext,
    engine: object,
    *,
    source_path: Path,
    source_key: str,
    speech_ranges: list[tuple[int, int]],
    scratch: Path,
) -> None:
    """Mux a speech-scrubbed preview video for the editor Before pane."""
    if not speech_ranges:
        return
    full_wav = scratch / "preview_audio.wav"
    await engine.extract_audio(str(source_path), str(full_wav))  # type: ignore[attr-defined]
    stems_dir = scratch / "preview_stems"
    stems_dir.mkdir(exist_ok=True)
    _vocals, no_vocals = await engine.split_stems(str(full_wav), str(stems_dir))  # type: ignore[attr-defined]
    selective_bed = scratch / "preview_speech_removed.wav"
    await engine.remove_recognized_speech(  # type: ignore[attr-defined]
        str(full_wav),
        no_vocals,
        speech_ranges,
        str(selective_bed),
    )
    voice_removed = scratch / "voice_removed.mp4"
    await engine.mux(str(source_path), str(selective_bed), str(voice_removed), None)  # type: ignore[attr-defined]
    await _upload_output(
        ctx,
        voice_removed,
        _voice_removed_key(ctx, source_key),
        "video/mp4",
    )


def _apply_corrected_texts(
    chunks: list[UtteranceChunk], corrected: dict[int, str]
) -> list[UtteranceChunk]:
    out: list[UtteranceChunk] = []
    for index, chunk in enumerate(chunks):
        text = (corrected.get(index) or chunk.text or "").strip()
        if not text:
            continue
        out.append(
            UtteranceChunk(
                start_ms=chunk.start_ms,
                end_ms=chunk.end_ms,
                text=text,
                speaker_id=chunk.speaker_id,
                words=chunk.words,
            )
        )
    return out

async def _upload_speech_ranges(
    ctx: JobContext,
    source_key: str,
    speech_ranges: list[tuple[int, int]],
    scratch: Path,
) -> None:
    if not speech_ranges:
        return
    ranges_path = scratch / "speech_ranges.json"
    ranges_path.write_text(
        json.dumps(
            [{"start_ms": start, "end_ms": end} for start, end in speech_ranges]
        ),
        encoding="utf-8",
    )
    await _upload_output(
        ctx,
        ranges_path,
        _speech_ranges_key(ctx, source_key),
        "application/json",
    )


async def _load_speech_ranges(
    ctx: JobContext, source_key: str, scratch: Path
) -> list[tuple[int, int]]:
    key = _speech_ranges_key(ctx, source_key)
    if await ctx.storage.head_object(key) is None:
        return []
    ranges_path = scratch / "speech_ranges.json"
    await ctx.storage.download_file(key, str(ranges_path))
    payload = json.loads(ranges_path.read_text(encoding="utf-8"))
    return [
        (int(item["start_ms"]), int(item["end_ms"]))
        for item in payload
        if int(item.get("end_ms", 0)) > int(item.get("start_ms", 0))
    ]


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
        transcribe_result = await _with_retries(
            ctx,
            lambda: engine.transcribe(str(asr_audio), str(project["source_lang"])),
            step="asr",
        )
        drafts = list(transcribe_result.drafts)
        if not drafts and not transcribe_result.words:
            raise PipelineError(errors.NO_SEGMENTS, "ASR produced no segments")
        await _upload_speech_ranges(
            ctx,
            str(project["source_key"]),
            transcribe_result.speech_ranges,
            scratch,
        )

        speaker_turns: list[tuple[int, int, str, str]] | None = None
        quality_warnings: list[str] = []
        if bool(project.get("diarization_enabled")):
            provider = create_diarization_provider(ctx.settings)
            if provider is None:
                quality_warnings.append(
                    "diarization_provider_unavailable_single_speaker_fallback"
                )
            else:
                await ctx.report(0.55, "diarization")
                turns = await _with_retries(
                    ctx, lambda: provider.diarize(str(asr_audio)), step="diarization"
                )
                speaker_turns = [
                    (turn.start_ms, turn.end_ms, turn.speaker_id, turn.text)
                    for turn in turns
                    if turn.end_ms > turn.start_ms
                ] or None

        await ctx.report(0.58, "segment_timing")
        # Breath/pause chunking on word timestamps (local high-quality path).
        # Never fall back to even time-slicing of diarization turns.
        if transcribe_result.words:
            chunks = build_breath_utterances(
                list(transcribe_result.words),
                speaker_turns,
                breath_pause_ms=650,
                max_duration_ms=max(
                    8000, round(ctx.settings.speech_segment_max_seconds * 1000 * 3)
                ),
                soft_pause_ms=400,
            )
            if not chunks:
                chunks = [
                    UtteranceChunk(
                        start_ms=draft.start_ms,
                        end_ms=draft.end_ms,
                        text=draft.text,
                        speaker_id="speaker_0",
                        words=(),
                    )
                    for draft in drafts
                ]
            chunks = merge_dangling_chunks(
                chunks,
                max_gap_ms=1500,
                max_duration_ms=13000,
            )
        else:
            # Mock / segment-only ASR: keep Whisper drafts (already filtered).
            chunks = [
                UtteranceChunk(
                    start_ms=draft.start_ms,
                    end_ms=draft.end_ms,
                    text=draft.text,
                    speaker_id="speaker_0",
                    words=(),
                )
                for draft in drafts
            ]
            if speaker_turns:
                # Prefer diarization turn text/timing when word tokens are absent.
                rebuilt: list[UtteranceChunk] = []
                for start, end, speaker, text in speaker_turns:
                    clean = (text or "").strip()
                    if end <= start:
                        continue
                    rebuilt.append(
                        UtteranceChunk(
                            start_ms=start,
                            end_ms=end,
                            text=clean or " ",
                            speaker_id=speaker or "speaker_0",
                            words=(),
                        )
                    )
                # Only replace when turns carry usable transcript text.
                if rebuilt and any(c.text.strip() for c in rebuilt):
                    chunks = [
                        UtteranceChunk(
                            start_ms=c.start_ms,
                            end_ms=c.end_ms,
                            text=c.text.strip(),
                            speaker_id=c.speaker_id,
                            words=(),
                        )
                        for c in rebuilt
                        if c.text.strip()
                    ]
        if not chunks:
            raise PipelineError(errors.NO_SEGMENTS, "ASR produced no segments")

        chunks = dedupe_boundary_overlaps(chunks)

        await ctx.report(0.62, "correct_asr")
        correction_items = [(i, c.text) for i, c in enumerate(chunks) if c.text.strip()]
        try:
            corrected = await _with_retries(
                ctx,
                lambda: engine.correct_transcript(
                    correction_items, str(project["source_lang"])
                ),
                step="correct_asr",
            )
            chunks = _apply_corrected_texts(chunks, corrected)
        except PipelineError as exc:
            logger.warning("ASR correction skipped: %s", exc)
            quality_warnings.append("asr_correction_skipped")

        chunks = dedupe_boundary_overlaps(chunks)
        if not chunks:
            raise PipelineError(errors.NO_SEGMENTS, "ASR produced no segments")

        speaker_assignments: list[tuple[str | None, bool]] = [
            (c.speaker_id or "speaker_0", False) for c in chunks
        ]
        if bool(project.get("diarization_enabled")) and speaker_turns is None:
            speaker_assignments = [(None, False) for _ in chunks]
        elif speaker_turns and all(not text.strip() for *_, text in speaker_turns):
            speaker_assignments = assign_speakers(
                [(c.start_ms, c.end_ms) for c in chunks],
                [
                    SpeakerTurn(start, end, speaker, text)
                    for start, end, speaker, text in speaker_turns
                ],
            )
        if any(overlap for _, overlap in speaker_assignments):
            quality_warnings.append("overlapping_speakers_use_default_voice")

        await ctx.report(0.72, "translate")
        full_source = " ".join(c.text.strip() for c in chunks if c.text.strip())
        document_translation = await _with_retries(
            ctx,
            lambda: engine.translate_document(
                full_source,
                str(project["source_lang"]),
                str(project["target_lang"]),
            ),
            step="translate",
        )
        aligned = align_document_translation(chunks, document_translation)
        chunks, aligned = place_long_units_by_timestamps(chunks, aligned)
        if len(speaker_assignments) != len(chunks):
            speaker_assignments = [
                (c.speaker_id or "speaker_0", False) for c in chunks
            ]

        drafts = [
            SegmentDraft(start_ms=c.start_ms, end_ms=c.end_ms, text=c.text)
            for c in chunks
        ]
        translations = {i: text for i, text in enumerate(aligned)}

        await ctx.report(0.85, "voice_removed_preview")
        try:
            await _build_and_upload_voice_removed(
                ctx,
                engine,
                source_path=source_path,
                source_key=str(project["source_key"]),
                speech_ranges=list(transcribe_result.speech_ranges),
                scratch=scratch,
            )
        except Exception as exc:  # noqa: BLE001 - preview must not fail extract
            logger.warning("voice-removed preview skipped: %s", exc)
            quality_warnings.append("voice_removed_preview_skipped")

        rows: list[Row] = [
            {
                "idx": i,
                "start_ms": d.start_ms,
                "end_ms": d.end_ms,
                "source_text": d.text,
                "target_text": translations.get(i, ""),
                "speaker_id": speaker_assignments[i][0]
                if i < len(speaker_assignments)
                else None,
                "speaker_overlap": speaker_assignments[i][1]
                if i < len(speaker_assignments)
                else False,
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
        segment_bounds = [
            (int(segment["start_ms"]), int(segment["end_ms"]))
            for segment in segments
            if str(segment.get("source_text", "")).strip()
            and int(segment["end_ms"]) > int(segment["start_ms"])
        ]
        saved_ranges = await _load_speech_ranges(
            ctx, str(project["source_key"]), scratch
        )
        speech_ranges = voice_removal_ranges(saved_ranges, segment_bounds)
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
        speakable_indices = {int(segment["idx"]) for segment in speakable}
        source_levels = await source_loudness_levels_async(
            speakable,
            speech_ranges,
            speakable_indices,
            lambda start_ms, end_ms: engine.measure_segment_loudness(
                vocals, start_ms, end_ms
            ),
        )

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
        target_lang = str(project["target_lang"])
        tone_style = str(project.get("tone_style") or "neutral")
        tolerance = ctx.settings.translation_timing_tolerance
        tts_concurrency = max(1, int(ctx.settings.tts_concurrency))
        sem = asyncio.Semaphore(tts_concurrency)

        async def _synthesize_primary(n: int, seg: dict) -> dict:
            async with sem:
                await ctx.report(0.45 + 0.20 * n / max(1, total), "tts")
                raw = clips_dir / f"seg_{seg['idx']}.{engine.tts_extension}"
                text = str(seg["target_text"]).strip()
                speaker_id = str(seg.get("speaker_id") or "")
                voice_id = (
                    speaker_voices.get(speaker_id, default_voice)
                    if not seg.get("speaker_overlap")
                    else default_voice
                )
                next_start = (
                    int(speakable[n + 1]["start_ms"]) if n + 1 < total else None
                )
                slot_s = safe_slot_seconds(
                    int(seg["start_ms"]), int(seg["end_ms"]), next_start
                )
                speak_speed = initial_speak_speed(
                    text,
                    slot_s,
                    target_lang,
                    min_speed=0.7,
                    max_speed=1.2,
                )
                await _with_retries(
                    ctx,
                    lambda t=text, p=str(raw), v=voice_id, s=speak_speed: engine.tts(
                        t,
                        v,
                        p,
                        tone_style,
                        target_lang,
                        s,
                    ),
                    step="tts",
                )
                clip_s = await engine.clip_duration_seconds(str(raw))
                measured_speed = speak_speed_for_slot(
                    clip_s,
                    slot_s,
                    min_speed=0.7,
                    max_speed=1.2,
                )
                # Rare corrective pass only when the estimate was clearly wrong.
                if abs(measured_speed - speak_speed) >= 0.12 and measured_speed > 1.03:
                    await _with_retries(
                        ctx,
                        lambda t=text, p=str(raw), v=voice_id, s=measured_speed: engine.tts(
                            t,
                            v,
                            p,
                            tone_style,
                            target_lang,
                            s,
                        ),
                        step="tts",
                    )
                    clip_s = await engine.clip_duration_seconds(str(raw))
                    speak_speed = measured_speed
                return {
                    "n": n,
                    "seg": seg,
                    "raw": raw,
                    "text": text,
                    "voice_id": voice_id,
                    "slot_s": slot_s,
                    "clip_s": clip_s,
                    "speak_speed": speak_speed,
                }

        primary = await asyncio.gather(
            *[_synthesize_primary(n, seg) for n, seg in enumerate(speakable)]
        )

        async def _maybe_rewrite(item: dict) -> dict:
            ratio = item["clip_s"] / item["slot_s"] if item["slot_s"] > 0 else 1.0
            # Residual rubberband covers moderate mismatch; only rewrite when
            # still badly off after synthesis-time speed.
            if not (ratio > 1 + tolerance or ratio < 1 - tolerance):
                return item
            async with sem:
                direction = "compress" if ratio > 1 else "expand"
                text = item["text"]
                raw = item["raw"]
                voice_id = item["voice_id"]
                slot_s = item["slot_s"]
                try:
                    text = await _with_retries(
                        ctx,
                        lambda t=text, d=direction: engine.adjust_translation(
                            t, target_lang, slot_s, d
                        ),
                        step="timing_rewrite",
                    )
                    speak_speed = initial_speak_speed(
                        text,
                        slot_s,
                        target_lang,
                        min_speed=0.7,
                        max_speed=1.2,
                    )
                    await _with_retries(
                        ctx,
                        lambda t=text, p=str(raw), v=voice_id, s=speak_speed: engine.tts(
                            t,
                            v,
                            p,
                            tone_style,
                            target_lang,
                            s,
                        ),
                        step="tts",
                    )
                    clip_s = await engine.clip_duration_seconds(str(raw))
                    item = {
                        **item,
                        "text": text,
                        "clip_s": clip_s,
                        "speak_speed": speak_speed,
                    }
                except PipelineError:
                    quality_warnings.append(
                        f"segment_{item['seg']['idx']}:timing_rewrite_failed"
                    )
                return item

        refined = await asyncio.gather(*[_maybe_rewrite(item) for item in primary])

        for item in sorted(refined, key=lambda row: int(row["n"])):
            await ctx.report(0.65 + 0.10 * item["n"] / max(1, total), "tts")
            seg = item["seg"]
            clip_s = float(item["clip_s"])
            slot_s = float(item["slot_s"])
            raw = item["raw"]
            decision = choose_fit_policy(
                clip_s,
                slot_s,
                min_tempo=ctx.settings.tts_min_tempo,
                atempo_max=ctx.settings.tts_atempo_max,
                max_speedup=ctx.settings.tts_max_speedup,
                rubberband_available=ffmpeg_has_rubberband(ctx.settings.ffmpeg_path)
                or bool(ctx.settings.rubberband_path),
            )
            if decision.warning:
                quality_warnings.append(f"segment_{seg['idx']}:{decision.warning}")
            tts_level = await engine.measure_clip_loudness(
                str(raw),
                0,
                max(1, int(clip_s * 1000)),
            )
            source_level = source_levels.get(int(seg["idx"]), tts_level)
            gain_db = matched_loudness_gain(source_level, tts_level)
            fitted = clips_dir / f"seg_{seg['idx']}_fit.wav"
            await engine.fit_clip(
                str(raw),
                str(fitted),
                decision.tempo,
                decision.backend,
                decision.output_seconds,
                gain_db,
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
