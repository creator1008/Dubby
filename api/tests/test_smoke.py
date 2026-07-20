"""Credential-free smoke tests: config, imports, routes, health endpoint.

These run in CI and on fresh checkouts without any external services.
"""

from __future__ import annotations

import importlib
import pkgutil

from fastapi.testclient import TestClient


def test_all_modules_import() -> None:
    import app

    for module in pkgutil.walk_packages(app.__path__, prefix="app."):
        importlib.import_module(module.name)


def test_settings_defaults() -> None:
    from app.config import Settings

    settings = Settings(_env_file=None)
    assert settings.app_env == "local"
    assert settings.worker_concurrency == 1
    assert settings.cors_origin_list == ["http://localhost:3000"]
    assert settings.r2_endpoint.endswith(".r2.cloudflarestorage.com")


def test_production_requires_secrets() -> None:
    import pytest
    from app.config import Settings

    with pytest.raises(ValueError):
        Settings(_env_file=None, app_env="production")


def test_expected_routes_registered() -> None:
    from app.main import create_app

    paths = set(create_app().openapi()["paths"])
    expected = {
        "/healthz",
        "/readyz",
        "/v1/projects",
        "/v1/projects/{project_id}",
        "/v1/projects/{project_id}/segments",
        "/v1/projects/{project_id}/jobs",
        "/v1/projects/{project_id}/output-url",
        "/v1/projects/{project_id}/import-url",
        "/v1/jobs/{job_id}",
        "/v1/jobs/{job_id}/cancel",
        "/v1/credits",
        "/v1/billing/checkout",
        "/v1/billing/webhook",
        "/v1/uploads/multipart",
        "/v1/uploads/multipart/{upload_id}/parts",
        "/v1/uploads/multipart/{upload_id}/complete",
        "/v1/uploads/multipart/{upload_id}/abort",
    }
    assert expected <= paths


def test_healthz_without_external_services(monkeypatch) -> None:
    """/healthz must respond without DB/R2/Supabase configured."""
    monkeypatch.setenv("DB_BACKEND", "supabase_rest")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-not-a-real-key")

    from app.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            resp = client.get("/healthz")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "ok"

            # Authenticated endpoints must reject anonymous requests.
            assert client.get("/v1/projects").status_code in (401, 403)
    finally:
        get_settings.cache_clear()


def test_filename_sanitizer() -> None:
    from app.storage.r2 import sanitize_filename

    assert sanitize_filename("my video (final).mp4") == "my_video_final_.mp4"
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("한국어영상.mp4") == "mp4" or sanitize_filename(
        "한국어영상.mp4"
    ).endswith(".mp4")
    assert sanitize_filename("") == "upload.bin"


def test_part_count_math() -> None:
    from app.config import Settings
    from app.storage.r2 import R2Storage

    storage = R2Storage(Settings(_env_file=None))
    part = Settings(_env_file=None).multipart_part_size_bytes
    assert storage.part_count_for(1) == 1
    assert storage.part_count_for(part) == 1
    assert storage.part_count_for(part + 1) == 2
