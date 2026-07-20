"""End-to-end pipeline tests in mock mode.

No external secrets, network, ffmpeg, or Demucs required: PIPELINE_MODE=mock
swaps every external dependency for deterministic local stand-ins, while the
orchestrator (progress, retries, cancellation, status transitions, scratch
cleanup) runs for real against in-memory repo/storage fakes.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.config import Settings
from app.worker import errors
from app.worker.errors import JobCancelled, PipelineError
from app.worker.pipeline import JobContext, run_dub, run_lipsync, run_transcribe


class FakeRepo:
    """Just the repository surface the worker pipeline touches."""

    def __init__(self, project: dict[str, Any]) -> None:
        self.project = project
        self.segments: list[dict[str, Any]] = []
        self.job_status = "running"
        self.progress_log: list[tuple[float, str | None]] = []

    async def update_job_progress(
        self, job_id: UUID, *, progress: float, message: str | None
    ) -> None:
        self.progress_log.append((progress, message))

    async def get_job_status(self, job_id: UUID) -> str | None:
        return self.job_status

    async def get_project_for_worker(self, project_id: UUID) -> dict | None:
        return dict(self.project)

    async def update_project_for_worker(
        self, project_id: UUID, fields: dict[str, Any]
    ) -> None:
        self.project.update(fields)

    async def replace_segments(self, project_id: UUID, segments: list[dict]) -> int:
        self.segments = [
            {"id": uuid4(), "project_id": project_id, **s} for s in segments
        ]
        return len(segments)

    async def list_segments_for_worker(self, project_id: UUID) -> list[dict]:
        return [dict(s) for s in self.segments]


class FakeStorage:
    """Local-filesystem stand-in for R2."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / key.replace("/", "__")

    def put(self, key: str, data: bytes) -> None:
        self._path(key).write_bytes(data)

    def output_key_for_source(self, source_key: str, filename: str) -> str:
        prefix = source_key.rsplit("/source/", 1)[0]
        return f"{prefix}/outputs/{filename}"

    async def head_object(self, key: str) -> dict | None:
        p = self._path(key)
        if not p.exists():
            return None
        return {"ContentLength": p.stat().st_size}

    async def download_file(self, key: str, destination: str) -> None:
        shutil.copyfile(self._path(key), destination)

    async def upload_file(
        self, source: str, key: str, content_type: str = "application/octet-stream"
    ) -> None:
        shutil.copyfile(source, self._path(key))


SOURCE_KEY = "users/u1/projects/p1/source/video.mp4"


def make_env(tmp_path: Path, **settings_kw: Any):
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        _env_file=None,
        pipeline_mode="mock",
        scratch_dir=str(scratch),
        pipeline_retry_backoff_seconds=0.0,
        **settings_kw,
    )
    project = {
        "id": uuid4(),
        "owner_id": uuid4(),
        "title": "t",
        "status": "uploaded",
        "source_lang": "ko",
        "target_lang": "en",
        "subtitle_mode": "target",
        "tone_style": "neutral",
        "diarization_enabled": False,
        "quality_warnings": [],
        "duration_seconds": None,
        "source_key": SOURCE_KEY,
        "output_key": None,
        "error": None,
    }
    repo = FakeRepo(project)
    storage = FakeStorage(tmp_path / "r2")
    storage.root.mkdir(parents=True, exist_ok=True)
    storage.put(SOURCE_KEY, b"\x00fake-video-bytes\x00" * 100)
    ctx = JobContext(
        job_id=uuid4(),
        project_id=project["id"],
        repo=repo,  # type: ignore[arg-type]
        storage=storage,  # type: ignore[arg-type]
        settings=settings,
    )
    return ctx, repo, storage, scratch


def _scratch_is_clean(scratch: Path) -> bool:
    return not any(scratch.iterdir())


def test_transcribe_mock_end_to_end(tmp_path: Path) -> None:
    ctx, repo, _, scratch = make_env(tmp_path)

    asyncio.run(run_transcribe(ctx))

    assert repo.project["status"] == "ready_for_edit"
    assert repo.project["error"] is None
    assert repo.project["duration_seconds"] == pytest.approx(8.0)

    assert len(repo.segments) == 3
    for i, seg in enumerate(repo.segments):
        assert seg["idx"] == i
        assert seg["end_ms"] > seg["start_ms"]
        assert seg["source_text"].startswith("Mock segment")
        assert seg["target_text"].startswith("[en] ")

    messages = [m for _, m in repo.progress_log]
    for expected in ("measuring_duration", "extracting_audio", "asr", "translate", "done"):
        assert expected in messages
    assert repo.progress_log[-1][0] == 1.0
    assert _scratch_is_clean(scratch)


def test_dub_mock_end_to_end(tmp_path: Path) -> None:
    ctx, repo, storage, scratch = make_env(tmp_path)
    asyncio.run(run_transcribe(ctx))

    asyncio.run(run_dub(ctx))

    assert repo.project["status"] == "completed"
    expected_key = "users/u1/projects/p1/outputs/dub_en.mp4"
    assert repo.project["output_key"] == expected_key
    # Mock mux copies the source container through.
    assert storage._path(expected_key).exists()

    messages = [m for _, m in repo.progress_log]
    for expected in (
        "stem_split",
        "voice_clone_tts",
        "tts",
        "mix_bgm",
        "burn_subtitles",
        "mux",
        "done",
    ):
        assert expected in messages
    assert _scratch_is_clean(scratch)


def test_multispeaker_and_lipsync_mock_end_to_end(tmp_path: Path) -> None:
    ctx, repo, storage, scratch = make_env(
        tmp_path, diarization_provider="mock", lipsync_provider="mock"
    )
    repo.project["diarization_enabled"] = True
    asyncio.run(run_transcribe(ctx))
    assert {s["speaker_id"] for s in repo.segments} == {"speaker_0", "speaker_1"}

    asyncio.run(run_dub(ctx))
    asyncio.run(run_lipsync(ctx))

    key = "users/u1/projects/p1/outputs/lipsync_en.mp4"
    assert repo.project["lipsync_output_key"] == key
    assert storage._path(key).exists()
    assert _scratch_is_clean(scratch)


def test_dub_without_subtitles_skips_burn(tmp_path: Path) -> None:
    ctx, repo, _, _ = make_env(tmp_path)
    asyncio.run(run_transcribe(ctx))
    repo.project["subtitle_mode"] = "none"
    repo.progress_log.clear()

    asyncio.run(run_dub(ctx))

    assert repo.project["status"] == "completed"
    assert "burn_subtitles" not in [m for _, m in repo.progress_log]


def test_dub_requires_translated_segments(tmp_path: Path) -> None:
    ctx, repo, _, scratch = make_env(tmp_path)

    with pytest.raises(PipelineError) as exc:
        asyncio.run(run_dub(ctx))

    assert exc.value.code == errors.NO_SEGMENTS
    assert repo.project["status"] == "failed"
    assert str(repo.project["error"]).startswith(errors.NO_SEGMENTS)
    assert _scratch_is_clean(scratch)


def test_transcribe_missing_source_object(tmp_path: Path) -> None:
    ctx, repo, storage, scratch = make_env(tmp_path)
    storage._path(SOURCE_KEY).unlink()

    with pytest.raises(PipelineError) as exc:
        asyncio.run(run_transcribe(ctx))

    assert exc.value.code == errors.SOURCE_MISSING
    assert repo.project["status"] == "failed"
    assert _scratch_is_clean(scratch)


def test_transcribe_rejects_oversized_source(tmp_path: Path) -> None:
    ctx, repo, _, scratch = make_env(tmp_path, max_source_bytes=10)

    with pytest.raises(PipelineError) as exc:
        asyncio.run(run_transcribe(ctx))

    assert exc.value.code == errors.SOURCE_TOO_LARGE
    assert repo.project["status"] == "failed"
    assert str(repo.project["error"]).startswith(errors.SOURCE_TOO_LARGE)
    assert _scratch_is_clean(scratch)


def test_transcribe_rejects_overlong_source(tmp_path: Path) -> None:
    # Mock probe reports 8s; force the limit below that.
    ctx, repo, _, _ = make_env(tmp_path, max_source_duration_seconds=5)

    with pytest.raises(PipelineError) as exc:
        asyncio.run(run_transcribe(ctx))

    assert exc.value.code == errors.SOURCE_TOO_LONG
    assert repo.project["status"] == "failed"


def test_cancellation_mid_transcribe_reverts_project(tmp_path: Path) -> None:
    ctx, repo, _, scratch = make_env(tmp_path)

    original_report = JobContext.report

    async def cancelling_report(self: JobContext, progress: float, message: str):
        if message == "asr":
            repo.job_status = "cancelled"
        await original_report(self, progress, message)

    JobContext.report = cancelling_report  # type: ignore[method-assign]
    try:
        with pytest.raises(JobCancelled):
            asyncio.run(run_transcribe(ctx))
    finally:
        JobContext.report = original_report  # type: ignore[method-assign]

    # Cancellation is not a failure: project returns to its pre-job status.
    assert repo.project["status"] == "uploaded"
    assert repo.segments == []
    assert _scratch_is_clean(scratch)


def test_retry_helper_retries_transient_errors(tmp_path: Path) -> None:
    from app.worker.pipeline import _with_retries

    ctx, _, _, _ = make_env(tmp_path, pipeline_step_retries=2)
    calls = {"n": 0}

    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise PipelineError("x", "transient", retryable=True)
        return "ok"

    assert asyncio.run(_with_retries(ctx, flaky, step="flaky")) == "ok"
    assert calls["n"] == 3


def test_retry_helper_does_not_retry_permanent_errors(tmp_path: Path) -> None:
    from app.worker.pipeline import _with_retries

    ctx, _, _, _ = make_env(tmp_path, pipeline_step_retries=5)
    calls = {"n": 0}

    async def broken() -> None:
        calls["n"] += 1
        raise PipelineError("x", "permanent", retryable=False)

    with pytest.raises(PipelineError):
        asyncio.run(_with_retries(ctx, broken, step="broken"))
    assert calls["n"] == 1


def test_progress_is_clamped(tmp_path: Path) -> None:
    ctx, repo, _, _ = make_env(tmp_path)
    asyncio.run(ctx.report(3.5, "queued"))
    asyncio.run(ctx.report(-1.0, "queued"))
    assert [p for p, _ in repo.progress_log] == [1.0, 0.0]
