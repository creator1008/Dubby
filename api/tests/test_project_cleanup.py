"""Tests for project cascade cleanup helpers."""

from __future__ import annotations

from uuid import uuid4

from app.project_cleanup import purge_local_project_files


def test_purge_local_project_files_removes_matching_dirs(
    tmp_path, monkeypatch
) -> None:
    project_id = uuid4()
    pid = str(project_id)
    compact = pid.replace("-", "")

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / pid).mkdir()
    (scratch / compact).mkdir()
    (scratch / f"dubby-job-{compact}-tmp").mkdir()
    (scratch / "unrelated").mkdir()

    monkeypatch.setattr(
        "app.project_cleanup._local_candidate_roots",
        lambda: [scratch],
    )

    removed = purge_local_project_files(project_id)
    assert not (scratch / pid).exists()
    assert not (scratch / compact).exists()
    assert not (scratch / f"dubby-job-{compact}-tmp").exists()
    assert (scratch / "unrelated").exists()
    assert len(removed) >= 2
