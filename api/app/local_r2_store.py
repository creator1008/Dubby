"""R2 object storage for the local step-1/2 verification server.

Persistent video/audio lives in Cloudflare R2 under ``local/runs/{run_id}/``
as a backup / hydrate source. By default the local scratch directory keeps
media between steps (see LOCAL_PURGE_SCRATCH); manifests always stay local.
"""

from __future__ import annotations

import mimetypes
import shutil
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig

from .config import get_settings

LOCAL_RUN_PREFIX = "local/runs"


def _require_r2_settings():
    settings = get_settings()
    if not (
        settings.r2_account_id
        and settings.r2_access_key_id
        and settings.r2_secret_access_key
    ):
        raise RuntimeError(
            "R2 자격증명이 없습니다. api/.env에 R2_ACCOUNT_ID, "
            "R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY를 설정해 주세요."
        )
    return settings


class LocalR2Store:
    def __init__(self) -> None:
        settings = _require_r2_settings()
        self._settings = settings
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.r2_endpoint,
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name=settings.r2_region,
            config=BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )

    @property
    def bucket(self) -> str:
        return self._settings.r2_bucket

    def object_key(self, run_id: str, relative: str) -> str:
        rel = relative.replace("\\", "/").lstrip("/")
        return f"{LOCAL_RUN_PREFIX}/{run_id}/{rel}"

    def run_prefix(self, run_id: str) -> str:
        return f"{LOCAL_RUN_PREFIX}/{run_id}/"

    def upload_file(self, run_id: str, local_path: Path, relative: str | None = None) -> str:
        rel = relative or local_path.name
        key = self.object_key(run_id, rel)
        content_type = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
        self._client.upload_file(
            str(local_path),
            self.bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )
        return key

    def presign_get(
        self,
        run_id: str,
        relative: str,
        *,
        download_filename: str | None = None,
    ) -> str:
        params: dict[str, str] = {
            "Bucket": self.bucket,
            "Key": self.object_key(run_id, relative),
        }
        if download_filename:
            params["ResponseContentDisposition"] = (
                f'attachment; filename="{download_filename}"'
            )
        return self._client.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=self._settings.download_expires_seconds,
        )

    def download_file(self, run_id: str, relative: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._client.download_file(
            self.bucket,
            self.object_key(run_id, relative),
            str(destination),
        )

    def get_object_bytes(self, run_id: str, relative: str) -> bytes:
        response = self._client.get_object(
            Bucket=self.bucket,
            Key=self.object_key(run_id, relative),
        )
        return response["Body"].read()

    def sync_run_to_r2(self, run_id: str, work_dir: Path) -> None:
        for path in sorted(work_dir.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() == ".part":
                continue
            rel = path.relative_to(work_dir).as_posix()
            self.upload_file(run_id, path, rel)

    def sync_run_from_r2(self, run_id: str, work_dir: Path) -> None:
        prefix = self.run_prefix(run_id)
        paginator = self._client.get_paginator("list_objects_v2")
        found = False
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                rel = key[len(prefix) :]
                if not rel:
                    continue
                found = True
                self.download_file(run_id, rel, work_dir / rel)
        if not found:
            raise RuntimeError(f"R2에서 run_id={run_id} 미디어를 찾을 수 없습니다.")

    def purge_work_dir_media(self, work_dir: Path) -> None:
        """Remove bulky media locally; keep small JSON manifests for quick lookup."""
        keep = {"manifest.json", "dub_voice_manifest.json"}
        for path in list(work_dir.iterdir()):
            if path.name in keep:
                continue
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)

    def delete_run(self, run_id: str) -> int:
        """Delete every object under ``local/runs/{run_id}/``. Returns deleted count."""
        prefix = self.run_prefix(run_id)
        deleted = 0
        to_delete: list[dict[str, str]] = []

        def _flush() -> None:
            nonlocal to_delete, deleted
            if not to_delete:
                return
            self._client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": to_delete},
            )
            deleted += len(to_delete)
            to_delete = []

        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                to_delete.append({"Key": obj["Key"]})
                if len(to_delete) >= 1000:
                    _flush()
        _flush()
        return deleted

    def list_run_ids(self) -> set[str]:
        """Return run_id folders present under ``local/runs/``."""
        prefix = f"{LOCAL_RUN_PREFIX}/"
        found: set[str] = set()
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix, Delimiter="/"):
            for entry in page.get("CommonPrefixes", []):
                raw = str(entry.get("Prefix") or "")
                # local/runs/{run_id}/
                rel = raw[len(prefix) :].strip("/")
                if rel and "/" not in rel:
                    found.add(rel)
        # Fallback when delimiter grouping is unavailable: parse object keys.
        if not found:
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    key = str(obj["Key"])
                    rest = key[len(prefix) :]
                    run_id = rest.split("/", 1)[0]
                    if run_id:
                        found.add(run_id)
        return found

    def delete_except(self, run_id: str, keep: set[str]) -> None:
        """Delete every object under the run prefix except the keep set (basename or relative)."""
        prefix = self.run_prefix(run_id)
        keep_normalized = {item.replace("\\", "/").lstrip("/") for item in keep}
        to_delete: list[dict[str, str]] = []

        def _flush() -> None:
            nonlocal to_delete
            if not to_delete:
                return
            self._client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": to_delete},
            )
            to_delete = []

        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                rel = key[len(prefix) :]
                if not rel or rel in keep_normalized:
                    continue
                to_delete.append({"Key": key})
                if len(to_delete) >= 1000:
                    _flush()
        _flush()

    def retain_final_videos(self, run_id: str, source_name: str) -> None:
        """After final mux, keep only the original source and dubbed output."""
        self.delete_except(run_id, {source_name, "dubbed_output.mp4"})
