"""Cloudflare R2 presigned multipart uploads via the S3-compatible API.

The API server never proxies file bytes. Clients upload directly to R2 with
presigned URLs; the server only creates/completes/aborts multipart uploads
and signs individual part URLs. boto3 calls are synchronous, so they are
dispatched to a thread from async handlers.

Key layout:
    users/{user_id}/projects/{project_id}/source/{filename}
    users/{user_id}/projects/{project_id}/outputs/...
"""

from __future__ import annotations

import asyncio
import math
import posixpath
import re
import unicodedata
from typing import Any
from uuid import UUID

import boto3
from botocore.config import Config as BotoConfig

from ..config import Settings

_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_filename(filename: str) -> str:
    """Normalize to a safe ASCII object-key component."""
    name = unicodedata.normalize("NFKD", filename)
    name = name.encode("ascii", "ignore").decode("ascii")
    name = posixpath.basename(name.replace("\\", "/"))
    name = _SAFE_FILENAME.sub("_", name).strip("._")
    return name or "upload.bin"


class R2Storage:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Any = None

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=self._settings.r2_endpoint,
                aws_access_key_id=self._settings.r2_access_key_id,
                aws_secret_access_key=self._settings.r2_secret_access_key,
                region_name=self._settings.r2_region,
                config=BotoConfig(
                    signature_version="s3v4",
                    # R2 requires path-style-off (virtual host style is not
                    # used with the account endpoint); boto's default "auto"
                    # addressing works with the R2 endpoint.
                    s3={"addressing_style": "path"},
                    retries={"max_attempts": 3, "mode": "standard"},
                ),
            )
        return self._client

    @property
    def bucket(self) -> str:
        return self._settings.r2_bucket

    @property
    def presign_expires_seconds(self) -> int:
        return self._settings.presign_expires_seconds

    # --- key builders ----------------------------------------------------------

    def source_key(self, user_id: UUID, project_id: UUID, filename: str) -> str:
        return f"users/{user_id}/projects/{project_id}/source/{sanitize_filename(filename)}"

    def output_key_for_source(self, source_key: str, filename: str) -> str:
        """Derive ``.../outputs/<filename>`` from a project's source key."""
        prefix = source_key.rsplit("/source/", 1)[0]
        return f"{prefix}/outputs/{sanitize_filename(filename)}"

    def user_prefix(self, user_id: UUID) -> str:
        return f"users/{user_id}/"

    def key_belongs_to_user(self, key: str, user_id: UUID) -> bool:
        return key.startswith(self.user_prefix(user_id))

    def part_count_for(self, size_bytes: int) -> int:
        return max(1, math.ceil(size_bytes / self._settings.multipart_part_size_bytes))

    # --- multipart lifecycle ------------------------------------------------------

    async def create_multipart_upload(self, key: str, content_type: str) -> str:
        resp = await asyncio.to_thread(
            self.client.create_multipart_upload,
            Bucket=self.bucket,
            Key=key,
            ContentType=content_type,
        )
        return resp["UploadId"]

    async def presign_upload_part(
        self, key: str, upload_id: str, part_number: int
    ) -> str:
        return await asyncio.to_thread(
            self.client.generate_presigned_url,
            "upload_part",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "UploadId": upload_id,
                "PartNumber": part_number,
            },
            ExpiresIn=self._settings.presign_expires_seconds,
        )

    async def complete_multipart_upload(
        self, key: str, upload_id: str, parts: list[dict[str, Any]]
    ) -> str | None:
        """``parts``: [{"PartNumber": int, "ETag": str}, ...] sorted ascending."""
        resp = await asyncio.to_thread(
            self.client.complete_multipart_upload,
            Bucket=self.bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={"Parts": parts},
        )
        return resp.get("Location")

    async def abort_multipart_upload(self, key: str, upload_id: str) -> None:
        await asyncio.to_thread(
            self.client.abort_multipart_upload,
            Bucket=self.bucket,
            Key=key,
            UploadId=upload_id,
        )

    # --- worker object transfer -------------------------------------------------

    async def head_object(self, key: str) -> dict[str, Any] | None:
        """Return object metadata (``ContentLength`` etc.) or None if missing."""
        try:
            return await asyncio.to_thread(
                self.client.head_object, Bucket=self.bucket, Key=key
            )
        except self.client.exceptions.ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    async def download_file(self, key: str, destination: str) -> None:
        await asyncio.to_thread(
            self.client.download_file, self.bucket, key, destination
        )

    async def upload_file(
        self, source: str, key: str, content_type: str = "application/octet-stream"
    ) -> None:
        await asyncio.to_thread(
            self.client.upload_file,
            source,
            self.bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )

    # --- simple presigns ------------------------------------------------------------

    async def presign_get(
        self,
        key: str,
        expires_in: int | None = None,
        download_filename: str | None = None,
    ) -> str:
        params = {"Bucket": self.bucket, "Key": key}
        if download_filename:
            filename = sanitize_filename(download_filename)
            params["ResponseContentDisposition"] = (
                f'attachment; filename="{filename}"'
            )
        return await asyncio.to_thread(
            self.client.generate_presigned_url,
            "get_object",
            Params=params,
            ExpiresIn=expires_in or self._settings.download_expires_seconds,
        )

    async def delete_prefix(self, prefix: str) -> None:
        """Best-effort recursive delete used when a project is removed."""

        def _delete() -> None:
            paginator = self.client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                objects = [{"Key": o["Key"]} for o in page.get("Contents", [])]
                if objects:
                    self.client.delete_objects(
                        Bucket=self.bucket, Delete={"Objects": objects}
                    )

        await asyncio.to_thread(_delete)
