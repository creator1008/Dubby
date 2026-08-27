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
    collapse_minor_speakers,
    create_diarization_provider,
    normalize_speaker_ids,
)
from .dub_quality import (
    cap_segment_ends_to_neighbors,
    final_voice_removal_bounds,
    matched_loudness_gain,
    next_start_by_segment_idx,
    source_loudness_levels_async,
    voice_removal_ranges,
)
from .dub_voice_assets import load_dub_voice_manifest, persist_dub_voice_assets
from .errors import JobCancelled, PipelineError
from .lipsync import create_lipsync_provider
from .media import ffmpeg_has_rubberband, validate_source
from .openai_client import SegmentDraft
from .subtitles import build_ass
from .timing import (
    choose_fit_policy,
    extend_end_ms,
    safe_slot_seconds,
    speak_speed_for_slot,
    speak_speed_matching_source,
)
from .utterance_pipeline import (
    UtteranceChunk,
    build_breath_utterances,
    dedupe_boundary_overlaps,
    merge_dangling_chunks,
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


async def _preferred_voice_id(ctx: JobContext, project: Row) -> str | None:
    """Most recently saved My Voice Box entry for the project owner, if any."""
    owner_raw = project.get("owner_id")
    if not owner_raw:
        return None
    try:
        owner_id = UUID(str(owner_raw))
    except (TypeError, ValueError):
        return None
    try:
        voices = await ctx.repo.list_user_voices(owner_id)
    except Exception:  # noqa: BLE001 - dubbing should fall back to env voice
        logger.warning("could not load user voice box for %s", owner_id)
        return None
    for row in voices:
        voice_id = str(row.get("elevenlabs_voice_id") or "").strip()
        if voice_id:
            return voice_id
    return None


def _project_dub_voice_ids(project: Row) -> list[str]:
    """Ordered ElevenLabs voice IDs selected on the new-dub form."""
    raw = project.get("dub_voice_ids")
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    return [str(v).strip() for v in raw if str(v).strip()]


async def _load_dub_meta_from_storage(ctx: JobContext, project: Row) -> dict:
    owner_raw = project.get("owner_id")
    if not owner_raw:
        return {}
    try:
        owner_id = UUID(str(owner_raw))
    except (TypeError, ValueError):
        return {}
    key = ctx.storage.project_meta_key(
        owner_id, ctx.project_id, "dub_voice_ids.json"
    )
    try:
        raw = await ctx.storage.download_bytes(key)
    except Exception:  # noqa: BLE001
        logger.warning("could not download dub meta for %s", ctx.project_id)
        return {}
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


async def _load_dub_voice_ids_from_storage(
    ctx: JobContext, project: Row
) -> list[str]:
    data = await _load_dub_meta_from_storage(ctx, project)
    voices = data.get("voice_ids")
    if not isinstance(voices, list):
        return []
    return [str(v).strip() for v in voices if str(v).strip()]


async def _resolve_dub_voices(
    ctx: JobContext,
    project: Row,
    speakers: list[str],
    *,
    engine: object | None = None,
    vocals_path: str | None = None,
    scratch: Path | None = None,
    speaker_ranges: dict[str, list[tuple[int, int]]] | None = None,
) -> tuple[str, dict[str, str], set[str], list[str]]:
    """Map speakers to Voice Box IDs or Instant Voice Clones (V2).

    Returns ``(default_voice, speaker_voices, protected_ids, warnings)``.
    Protected ids (My Voice Box / env / limit fallbacks) are never deleted.
    """
    meta = await _load_dub_meta_from_storage(ctx, project)
    voice_mode = str(project.get("voice_mode") or meta.get("voice_mode") or "voice_box")
    if voice_mode not in {"voice_box", "auto_clone"}:
        voice_mode = "voice_box"
    voice_warnings: list[str] = []

    if voice_mode == "auto_clone":
        if engine is None or vocals_path is None or scratch is None:
            raise PipelineError(
                errors.VOICE_MISSING,
                "Auto voice clone requires vocals stem and engine.",
            )
        speaker_voices: dict[str, str] = {}
        protected: set[str] = set()
        if ctx.settings.elevenlabs_voice_id:
            protected.add(str(ctx.settings.elevenlabs_voice_id).strip())
        sample_dir = scratch / "ivc_samples"
        sample_dir.mkdir(exist_ok=True)
        targets = speakers or ["speaker_1"]
        used_limit_fallback = False
        for speaker in targets:
            ranges = (speaker_ranges or {}).get(speaker) or []
            sample_path = sample_dir / f"{speaker}.mp3"
            voice_id, limit_fallback = await engine.prepare_voice(  # type: ignore[attr-defined]
                vocals_path,
                str(sample_dir),
                f"dubby-{ctx.project_id}-{speaker}"[:80],
                ranges_ms=ranges or None,
                preferred_voice_id=None,
                force_clone=True,
                sample_out=str(sample_path),
            )
            speaker_voices[speaker] = voice_id
            if limit_fallback:
                used_limit_fallback = True
                protected.add(voice_id)
        if not speaker_voices:
            raise PipelineError(
                errors.VOICE_MISSING,
                "Auto voice clone produced no voices.",
            )
        if used_limit_fallback:
            voice_warnings.append("voice_add_edit_limit_default_voice")
        default_voice = next(iter(speaker_voices.values()))
        return default_voice, speaker_voices, protected, voice_warnings

    selected = _project_dub_voice_ids(project)
    if not selected:
        selected = await _load_dub_voice_ids_from_storage(ctx, project)
    if not selected:
        fallback = await _preferred_voice_id(ctx, project)
        if fallback:
            selected = [fallback]
    if not selected and ctx.settings.elevenlabs_voice_id:
        selected = [str(ctx.settings.elevenlabs_voice_id).strip()]
    selected = [v for v in selected if v]
    if not selected:
        raise PipelineError(
            errors.VOICE_MISSING,
            "No dubbing voice selected. Choose My Voice Box voices or enable auto clone.",
        )

    default_voice = selected[0]
    protected = set(selected)
    if ctx.settings.elevenlabs_voice_id:
        protected.add(str(ctx.settings.elevenlabs_voice_id).strip())

    mapped: dict[str, str] = {}
    for index, speaker in enumerate(speakers):
        mapped[speaker] = selected[index] if index < len(selected) else default_voice
    return default_voice, mapped, protected, voice_warnings


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
        original = (chunk.text or "").strip()
        raw = corrected.get(index)
        if raw is None:
            text = original
        else:
            text = str(raw).strip() or original
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


def _rewrite_diverges(original: str, rewritten: str) -> bool:
    """True when a timing rewrite likely invents or drops meaning."""
    import re

    def tokens(value: str) -> set[str]:
        return {t.lower() for t in re.findall(r"[A-Za-z0-9\uac00-\ud7af]+", value)}

    src = tokens(original)
    dst = tokens(rewritten)
    if not src or not dst:
        return True
    # Too many brand-new tokens → invented speech not in the editor line.
    novel = dst - src
    if len(novel) >= max(2, len(src) // 2):
        return True
    # Collapsed to almost nothing relative to source.
    if len(dst) < max(1, len(src) // 3):
        return True
    return False

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
        source_lang = str(project.get("source_lang") or "").strip()
        if bool(project.get("diarization_enabled")):
            provider = create_diarization_provider(ctx.settings)
            if provider is None:
                quality_warnings.append(
                    "diarization_provider_unavailable_single_speaker_fallback"
                )
            else:
                await ctx.report(0.55, "diarization")
                turns = await _with_retries(
                    ctx,
                    lambda: provider.diarize(str(asr_audio), language=source_lang or None),
                    step="diarization",
                )
                turns = normalize_speaker_ids(
                    [turn for turn in turns if turn.end_ms > turn.start_ms]
                )
                speaker_turns = [
                    (turn.start_ms, turn.end_ms, turn.speaker_id, turn.text)
                    for turn in turns
                ] or None
                if speaker_turns is None:
                    quality_warnings.append("diarization_empty_turns_single_speaker_fallback")

        await ctx.report(0.58, "segment_timing")
        # Split on real voice gaps (>= breath_pause_ms), even inside one sentence.
        breath_ms = max(400, int(getattr(ctx.settings, "breath_pause_ms", 650)))
        if transcribe_result.words:
            chunks = build_breath_utterances(
                list(transcribe_result.words),
                speaker_turns,
                breath_pause_ms=breath_ms,
                max_duration_ms=max(
                    8000, round(ctx.settings.speech_segment_max_seconds * 1000 * 2)
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
            # Only re-glue micro-scraps (soft-split crumbs). Never undo a
            # deliberate breath/gap split the user expects to stay separate.
            merge_gap_ms = min(400, max(200, breath_ms // 2))
            chunks = merge_dangling_chunks(
                chunks,
                max_gap_ms=merge_gap_ms,
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
        elif speaker_turns:
            # Always resolve speakers by time overlap. Keep majority speaker even
            # when overlap is soft — never clear speaker_id (that collapses voices).
            timed = assign_speakers(
                [(c.start_ms, c.end_ms) for c in chunks],
                [
                    SpeakerTurn(start, end, speaker, text)
                    for start, end, speaker, text in speaker_turns
                ],
            )
            refined: list[tuple[str | None, bool]] = []
            for chunk, (spk, overlap) in zip(chunks, timed):
                refined.append((spk or chunk.speaker_id or "speaker_1", overlap))
            # Drop tiny spurious labels (e.g. 1–2 micro turns as speaker_2).
            speaker_assignments = collapse_minor_speakers(
                refined,
                [(c.start_ms, c.end_ms) for c in chunks],
            )
        if any(overlap for _, overlap in speaker_assignments):
            quality_warnings.append("overlapping_speakers_majority_voice")

        await ctx.report(0.72, "translate")
        # Per-segment translation with full-transcript context. Document-level
        # translate + LLM align used to bleed clauses onto neighbor idxs
        # (e.g. "I've been thinking…" attached to the next short line).
        full_source = "\n".join(
            f"[{i}] {c.text.strip()}" for i, c in enumerate(chunks) if c.text.strip()
        )
        translate_items = [
            (
                i,
                c.text,
                max(0.35, (c.end_ms - c.start_ms) / 1000.0),
            )
            for i, c in enumerate(chunks)
        ]
        translations = await _with_retries(
            ctx,
            lambda: engine.translate_batch(
                translate_items,
                str(project["source_lang"]),
                str(project["target_lang"]),
                document_context=full_source,
            ),
            step="translate",
        )

        drafts = [
            SegmentDraft(start_ms=c.start_ms, end_ms=c.end_ms, text=c.text)
            for c in chunks
        ]

        # Skip voice-removed preview video: a second Demucs + mux pass was the
        # slowest part of extract and is unused once Before uses the original.
        await ctx.report(0.85, "prepare_segments")

        rows: list[Row] = [
            {
                "idx": i,
                "start_ms": d.start_ms,
                "end_ms": d.end_ms,
                "source_text": d.text,
                "target_text": (translations.get(i) or "").strip(),
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
    """dub (V2): duck original bed -> voice resolve -> TTS -> fit/mix -> mux.

    Project: ready_for_edit -> dubbing -> completed (failed on error).
    Mix bed is the original audio with source-language spans ducked; other
    languages passthrough. Voices come from My Voice Box or auto IVC.
    """
    engine = create_engine(ctx.settings, heartbeat=ctx.heartbeat)
    scratch = _make_scratch(ctx)
    voice_ids: set[str] = set()
    protected_voices: set[str] = set()
    try:
        project = await _load_project(ctx)
        segments = await ctx.repo.list_segments_for_worker(ctx.project_id)
        # Editor-saved speak rates / original ends may live in R2 when DB lacks them.
        try:
            from .dub_voice_assets import load_dub_voice_manifest

            manifest_speeds = await load_dub_voice_manifest(
                ctx.storage, str(project.get("source_key") or "") or None
            )
            for row in segments:
                try:
                    idx = int(row["idx"])
                except (KeyError, TypeError, ValueError):
                    continue
                meta = manifest_speeds.get(idx) or {}
                if row.get("speak_speed") is None:
                    speed = meta.get("speak_speed")
                    try:
                        speed_f = float(speed) if speed is not None else None
                    except (TypeError, ValueError):
                        speed_f = None
                    if speed_f is not None and speed_f > 0:
                        row["speak_speed"] = speed_f

                # Editor-saved emotion tone wins over acoustic re-detection.
                saved_tone = row.get("emotion_tone") or meta.get("emotion_tone")
                if saved_tone:
                    row["emotion_tone"] = str(saved_tone).strip()

                # Freeze original ASR end; never overwrite with a rate-adjusted end_ms.
                source_end = row.get("source_end_ms")
                if source_end is None:
                    source_end = meta.get("source_end_ms")
                try:
                    source_end_i = int(source_end) if source_end is not None else None
                except (TypeError, ValueError):
                    source_end_i = None
                try:
                    start_i = int(row["start_ms"])
                    end_i = int(row["end_ms"])
                except (KeyError, TypeError, ValueError):
                    continue
                if source_end_i is None or source_end_i <= start_i:
                    # Recover original span when only shortened translation end remains.
                    try:
                        speed_for_recover = float(row.get("speak_speed") or 1.0)
                    except (TypeError, ValueError):
                        speed_for_recover = 1.0
                    translation_ms = max(120, end_i - start_i)
                    if abs(speed_for_recover - 1.0) >= 0.001:
                        source_end_i = start_i + int(
                            round(translation_ms * max(0.5, speed_for_recover))
                        )
                    else:
                        source_end_i = end_i
                row["source_end_ms"] = source_end_i

                # Translation end = original_duration / speak_speed (capped later by fit).
                try:
                    locked_speed = float(row.get("speak_speed") or 0)
                except (TypeError, ValueError):
                    locked_speed = 0.0
                if locked_speed > 0 and source_end_i > start_i:
                    source_ms = max(120, source_end_i - start_i)
                    desired_end = start_i + int(round(source_ms / locked_speed))
                    row["end_ms"] = max(start_i + 120, desired_end)
            # Cap slowed-down translation ends so they do not cross the next stamp.
            ordered = sorted(
                segments,
                key=lambda r: int(r.get("idx", 0)),
            )
            for i, row in enumerate(ordered):
                try:
                    start_i = int(row["start_ms"])
                    end_i = int(row["end_ms"])
                except (KeyError, TypeError, ValueError):
                    continue
                if i + 1 < len(ordered):
                    try:
                        next_start = int(ordered[i + 1]["start_ms"]) - 80
                    except (KeyError, TypeError, ValueError):
                        continue
                    capped = max(start_i + 120, min(end_i, next_start))
                    if capped != end_i:
                        row["end_ms"] = capped
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not load editor speak speeds: %s", exc)
        translated = [s for s in segments if str(s.get("target_text", "")).strip()]
        if not translated:
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

        source_lang = str(project["source_lang"])
        # V2: only dub segments that match the declared source language.
        from .language_passthrough import should_passthrough

        speakable = [
            s
            for s in translated
            if not should_passthrough(str(s.get("source_text", "")), source_lang)
        ]
        if not speakable:
            raise PipelineError(
                errors.NO_SEGMENTS,
                "no source-language segments to dub (others kept as original audio)",
            )
        # Full timeline (incl. passthrough) so slots never steal a neighbor's audio.
        next_start_by_idx = next_start_by_segment_idx(segments)

        # Demucs stems: IVC samples / loudness; mix bed rebuilt after TTS timing.
        # Soft volume ducking left original speech audible under the dub (phuc).
        await ctx.report(0.15, "stem_split")
        stems_dir = scratch / "stems"
        stems_dir.mkdir()
        vocals, no_vocals = await engine.split_stems(str(full_wav), str(stems_dir))

        segment_bounds = [
            (
                int(segment["start_ms"]),
                int(segment.get("source_end_ms") or segment["end_ms"]),
            )
            for segment in speakable
            if int(segment.get("source_end_ms") or segment["end_ms"])
            > int(segment["start_ms"])
        ]
        if not segment_bounds:
            raise PipelineError(
                errors.NO_SEGMENTS,
                "no valid timing ranges for dubbed speech",
            )
        saved_ranges = await _load_speech_ranges(
            ctx, str(project["source_key"]), scratch
        )
        speakable_indices = {int(segment["idx"]) for segment in speakable}
        # Word-level speech ranges only — full segment bounds dilute soft speech
        # with silence and exaggerate loudness gaps across speakers.
        speech_for_levels = saved_ranges if saved_ranges else segment_bounds
        source_levels = await source_loudness_levels_async(
            [
                {
                    **segment,
                    # Match loudness to the original ASR span, not a rate-shortened end.
                    "end_ms": int(
                        segment.get("source_end_ms") or segment["end_ms"]
                    ),
                }
                for segment in speakable
            ],
            speech_for_levels,
            speakable_indices,
            # Same meter as TTS clips (volumedetect) so gain is apples-to-apples.
            lambda start_ms, end_ms: engine.measure_clip_loudness(
                vocals, start_ms, end_ms
            ),
        )

        from .emotion import detect_emotions_for_segments, normalize_emotion_tone

        project_tone = normalize_emotion_tone(
            str(project.get("tone_style") or "calm")
        )
        emotion_by_idx = await asyncio.to_thread(
            detect_emotions_for_segments,
            str(vocals),
            speakable,
            fallback=project_tone,
        )
        for segment in speakable:
            try:
                idx = int(segment["idx"])
            except (KeyError, TypeError, ValueError):
                continue
            saved = str(segment.get("emotion_tone") or "").strip()
            if saved:
                segment["emotion_tone"] = normalize_emotion_tone(
                    saved, fallback=project_tone
                )
            else:
                segment["emotion_tone"] = emotion_by_idx.get(idx, project_tone)

        await ctx.report(0.40, "dub_voice_tts")
        speakers: list[str] = []
        seen_speakers: set[str] = set()
        for seg in speakable:
            sid = str(seg.get("speaker_id") or "").strip()
            if not sid or seg.get("speaker_overlap") or sid in seen_speakers:
                continue
            seen_speakers.add(sid)
            speakers.append(sid)
        speaker_ranges: dict[str, list[tuple[int, int]]] = {}
        for seg in speakable:
            sid = str(seg.get("speaker_id") or "").strip() or "speaker_1"
            speaker_ranges.setdefault(sid, []).append(
                (int(seg["start_ms"]), int(seg["end_ms"]))
            )
        default_voice, speaker_voices, protected_voices, voice_warnings = (
            await _resolve_dub_voices(
                ctx,
                project,
                speakers,
                engine=engine,
                vocals_path=str(vocals),
                scratch=scratch,
                speaker_ranges=speaker_ranges,
            )
        )
        voice_ids.add(default_voice)
        voice_ids.update(speaker_voices.values())

        placed_clips: list[tuple[str, int]] = []
        quality_warnings = list(project.get("quality_warnings") or [])
        quality_warnings.extend(voice_warnings)
        clips_dir = scratch / "clips"
        clips_dir.mkdir()
        total = len(speakable)
        target_lang = str(project["target_lang"])
        tone_style = str(project.get("tone_style") or "calm")
        tolerance = ctx.settings.translation_timing_tolerance
        tts_concurrency = max(1, int(ctx.settings.tts_concurrency))
        sem = asyncio.Semaphore(tts_concurrency)

        async def _synthesize_primary(n: int, seg: dict) -> dict:
            async with sem:
                await ctx.report(0.45 + 0.20 * n / max(1, total), "tts")
                raw = clips_dir / f"seg_{seg['idx']}.{engine.tts_extension}"
                text = str(seg["target_text"]).strip()
                source_text = str(seg.get("source_text") or "").strip()
                speaker_id = str(seg.get("speaker_id") or "")
                # Always map by speaker slot; soft overlaps still keep majority voice.
                voice_id = speaker_voices.get(speaker_id, default_voice)
                next_start = next_start_by_idx.get(int(seg["idx"]))
                end_ms = int(seg["end_ms"])
                slot_s = safe_slot_seconds(
                    int(seg["start_ms"]), end_ms, next_start
                )
                saved_speed = seg.get("speak_speed")
                try:
                    saved_f = float(saved_speed) if saved_speed is not None else None
                except (TypeError, ValueError):
                    saved_f = None
                speak_speed_locked = saved_f is not None and saved_f > 0
                # Editor rate to show/persist after the job (may be outside EL bounds).
                editor_speak_speed = (
                    float(saved_f) if speak_speed_locked else 1.0
                )
                # ElevenLabs TTS rate (clamped).
                speak_speed = max(0.7, min(1.2, editor_speak_speed))
                segment_tone = str(
                    seg.get("emotion_tone") or tone_style or "calm"
                )
                await _with_retries(
                    ctx,
                    lambda t=text, p=str(raw), v=voice_id, s=speak_speed, tone=segment_tone: engine.tts(
                        t,
                        v,
                        p,
                        tone,
                        target_lang,
                        s,
                    ),
                    step="tts",
                )
                if ctx.settings.voice_changer_after_tts:
                    sts_out = clips_dir / f"seg_{seg['idx']}_sts.{engine.tts_extension}"
                    await _with_retries(
                        ctx,
                        lambda src=str(raw), dst=str(sts_out), v=voice_id: engine.speech_to_speech(
                            src, v, dst
                        ),
                        step="voice_changer",
                    )
                    raw = sts_out
                clip_s = await engine.clip_duration_seconds(str(raw))
                return {
                    "n": n,
                    "seg": seg,
                    "raw": raw,
                    "text": text,
                    "source_text": source_text,
                    "voice_id": voice_id,
                    "slot_s": slot_s,
                    "end_ms": end_ms,
                    "next_start": next_start,
                    "clip_s": clip_s,
                    # Keep the editor value for UI; TTS may have been clamped.
                    "speak_speed": editor_speak_speed,
                    "tts_speak_speed": speak_speed,
                    "speak_speed_locked": speak_speed_locked,
                    "emotion_tone": segment_tone,
                    "source_end_ms": int(
                        seg.get("source_end_ms")
                        or end_ms
                    ),
                }

        primary = await asyncio.gather(
            *[_synthesize_primary(n, seg) for n, seg in enumerate(speakable)]
        )

        async def _fit_natural_delivery(item: dict) -> dict:
            """Compress translation, extend stamp, then speed up — avoid cutting.

            Truncation is a last resort handled later by ``choose_fit_policy``
            only when even max residual tempo cannot fit the slot.
            """
            ratio = item["clip_s"] / item["slot_s"] if item["slot_s"] > 0 else 1.0
            if ratio <= 1 + tolerance:
                return item

            async with sem:
                text = item["text"]
                raw = item["raw"]
                voice_id = item["voice_id"]
                slot_s = float(item["slot_s"])
                end_ms = int(item["end_ms"])
                next_start = item["next_start"]
                source_text = item["source_text"]
                speak_speed = float(item.get("speak_speed") or 1.0)
                tts_speak_speed = float(
                    item.get("tts_speak_speed") or speak_speed or 1.0
                )
                locked = bool(item.get("speak_speed_locked"))
                segment_tone = str(
                    item.get("emotion_tone")
                    or item.get("seg", {}).get("emotion_tone")
                    or tone_style
                    or "calm"
                )

                # 1) Compress translation (same meaning, fewer spoken syllables).
                if ctx.settings.translation_timing_rewrite:
                    try:
                        compressed = await _with_retries(
                            ctx,
                            lambda t=text: engine.adjust_translation(
                                t, target_lang, slot_s, "compress"
                            ),
                            step="timing_rewrite",
                        )
                        rewritten = str(compressed or "").strip()
                        if rewritten and not _rewrite_diverges(text, rewritten):
                            text = rewritten
                            if not locked:
                                speak_speed = speak_speed_matching_source(
                                    source_text,
                                    source_lang,
                                    text,
                                    target_lang,
                                    slot_s,
                                    min_speed=0.7,
                                    max_speed=1.2,
                                )
                                tts_speak_speed = max(0.7, min(1.2, speak_speed))
                            await _with_retries(
                                ctx,
                                lambda t=text, p=str(raw), v=voice_id, s=tts_speak_speed, tone=segment_tone: engine.tts(
                                    t,
                                    v,
                                    p,
                                    tone,
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
                                "tts_speak_speed": tts_speak_speed,
                            }
                            if clip_s / slot_s <= 1 + tolerance:
                                return item
                        else:
                            quality_warnings.append(
                                f"segment_{item['seg']['idx']}:timing_rewrite_rejected"
                            )
                    except PipelineError:
                        quality_warnings.append(
                            f"segment_{item['seg']['idx']}:timing_rewrite_failed"
                        )

                # 2) Extend translation end into trailing silence (no overlap).
                # Never touch source_end_ms. Skip when the editor locked speak_speed
                # so translation_duration stays original_duration / speak_speed.
                clip_s = float(item["clip_s"])
                need = max(0.0, clip_s - slot_s)
                if need > 0.05 and not locked:
                    new_end = extend_end_ms(
                        int(item["seg"]["start_ms"]),
                        end_ms,
                        next_start if next_start is None else int(next_start),
                        need,
                        pad_ms=80,
                    )
                    if new_end > end_ms:
                        end_ms = new_end
                        slot_s = safe_slot_seconds(
                            int(item["seg"]["start_ms"]), end_ms, next_start
                        )
                        item = {**item, "end_ms": end_ms, "slot_s": slot_s}
                        quality_warnings.append(
                            f"segment_{item['seg']['idx']}:slot_extended"
                        )
                        if clip_s / slot_s <= 1 + tolerance:
                            return item

                # 3) Speed up TTS only when the editor did not lock speak_speed.
                # Locked rates rely on residual tempo fit instead of re-synthesis.
                clip_s = float(item["clip_s"])
                slot_s = float(item["slot_s"])
                if (
                    not locked
                    and slot_s > 0
                    and clip_s / slot_s > 1 + tolerance
                ):
                    current_speed = float(item.get("tts_speak_speed") or item.get("speak_speed") or 1.0)
                    # Estimate duration at speed=1.0, then pick EL speed to fit slot.
                    natural_s = clip_s * max(current_speed, 0.01)
                    faster = speak_speed_for_slot(
                        natural_s,
                        slot_s,
                        min_speed=0.7,
                        max_speed=1.2,
                        tolerance=tolerance,
                    )
                    if faster >= current_speed + 0.08:
                        speak_speed = faster
                        tts_speak_speed = faster
                        await _with_retries(
                            ctx,
                            lambda t=text, p=str(raw), v=voice_id, s=tts_speak_speed, tone=segment_tone: engine.tts(
                                t,
                                v,
                                p,
                                tone,
                                target_lang,
                                s,
                            ),
                            step="tts",
                        )
                        clip_s = await engine.clip_duration_seconds(str(raw))
                        item = {
                            **item,
                            "clip_s": clip_s,
                            "speak_speed": speak_speed,
                            "tts_speak_speed": tts_speak_speed,
                        }
                        quality_warnings.append(
                            f"segment_{item['seg']['idx']}:speak_speed_fit"
                        )
                return item

        refined = await asyncio.gather(
            *[_fit_natural_delivery(item) for item in primary]
        )

        # Persist compressed lines + extended ends + speak speeds for the editor.
        persist_updates: list[tuple] = []
        end_by_idx: dict[int, int] = {}
        for item in refined:
            seg = item["seg"]
            idx = int(seg["idx"])
            end_by_idx[idx] = int(item["end_ms"])
            if seg.get("id") is None:
                continue
            # Keep editor translations; timing rewrite is burn/speak-only.
            persist_updates.append(
                (
                    seg["id"],
                    str(seg.get("target_text") or item.get("text") or ""),
                    None,
                    int(item["end_ms"]),
                    float(item.get("speak_speed") or 1.0),
                )
            )
        if persist_updates:
            try:
                owner_id = project.get("owner_id")
                if owner_id is not None:
                    await ctx.repo.update_segment_texts(
                        UUID(str(owner_id)),
                        ctx.project_id,
                        [
                            (
                                UUID(str(seg_id))
                                if not isinstance(seg_id, UUID)
                                else seg_id,
                                target,
                                source,
                                end_ms,
                                speak_speed,
                            )
                            for seg_id, target, source, end_ms, speak_speed in persist_updates
                        ],
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("persist segment timing/speeds failed: %s", exc)
                quality_warnings.append("segment_timing_not_persisted")

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
            # Gain-only preview keeps editor speak-rate scrubbing meaningful.
            preview = clips_dir / f"seg_{seg['idx']}_preview.wav"
            await engine.fit_clip(
                str(raw),
                str(preview),
                1.0,
                "atempo",
                None,
                gain_db,
            )
            fitted = clips_dir / f"seg_{seg['idx']}_fit.wav"
            await engine.fit_clip(
                str(raw),
                str(fitted),
                decision.tempo,
                decision.backend,
                decision.output_seconds,
                gain_db,
            )
            item["gain_db"] = gain_db
            item["source_level_db"] = source_level
            item["tts_level_db"] = tts_level
            item["preview_clip"] = str(preview)
            placed_clips.append((str(fitted), int(seg["start_ms"])))

        try:
            await persist_dub_voice_assets(
                ctx.storage,
                source_key=str(project.get("source_key") or ""),
                items=list(refined),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("persist dub voice assets failed: %s", exc)
            quality_warnings.append("dub_voice_preview_not_persisted")

        # Rebuild mix bed after TTS slot extensions so scrub covers spoken audio.
        await ctx.report(0.76, "voice_removal")
        final_bounds = final_voice_removal_bounds(list(refined), next_start_by_idx)
        if not final_bounds:
            final_bounds = segment_bounds
        removal_ranges = voice_removal_ranges(
            saved_ranges,
            final_bounds,
            fill_interiors=True,
        )
        selective_bed = scratch / "speech_removed_bed.wav"
        await engine.remove_recognized_speech(
            str(full_wav),
            no_vocals,
            removal_ranges,
            str(selective_bed),
            no_vocals_in_mask=0.15,
        )

        await ctx.report(0.78, "mix_bgm")
        mixed_wav = scratch / "mixed.wav"
        await engine.mix(str(selective_bed), placed_clips, str(mixed_wav))

        ass_path: str | None = None
        subtitle_mode = str(project.get("subtitle_mode") or "none")
        # Spoken lines may use timing-rewritten text; passthrough keeps editor copy.
        spoken_by_idx = {
            int(item["seg"]["idx"]): str(item["text"]).strip()
            for item in refined
            if str(item.get("text") or "").strip()
        }
        ass_rows: list[dict] = []
        for row in segments:
            copied = dict(row)
            idx = int(row["idx"])
            if idx in end_by_idx:
                copied["end_ms"] = end_by_idx[idx]
            if subtitle_mode == "target" and idx in spoken_by_idx:
                copied["target_text"] = spoken_by_idx[idx]
            ass_rows.append(copied)
        cap_segment_ends_to_neighbors(ass_rows)
        ass_text = build_ass(ass_rows, subtitle_mode)  # type: ignore[arg-type]
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
                await engine.cleanup_voice(voice_id, protected_ids=protected_voices)
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
