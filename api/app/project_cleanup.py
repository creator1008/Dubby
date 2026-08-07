"""Cascade cleanup when a dubbing-history project is deleted.

Removes Cloudflare R2 objects, local scratch copies, and (via the repository)
Supabase rows for the project.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from uuid import UUID

from .config import get_settings
from .storage.r2 import R2Storage

logger = logging.getLogger("dubby.project_cleanup")

# Repo root: api/app/project_cleanup.py → parents[2] == repo
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _local_candidate_roots() -> list[Path]:
    roots: list[Path] = []
    settings = get_settings()
    scratch = (settings.scratch_dir or "").strip()
    if scratch:
        roots.append(Path(scratch))
    roots.extend(
        [
            _REPO_ROOT / ".local-data" / "scratch",
            _REPO_ROOT / ".local-data" / "step12",
        ]
    )
    # Deduplicate while preserving order.
    seen: set[str] = set()
    unique: list[Path] = []
    for root in roots:
        key = str(root.resolve()) if root.exists() else str(root)
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def purge_local_project_files(project_id: UUID) -> list[str]:
    """Delete local folders that belong to this project id.

    Matches ``{project_id}`` (with dashes) and the compact hex form under known
    scratch roots. Returns paths that were removed (or attempted).
    """
    pid = str(project_id)
    compact = pid.replace("-", "").lower()
    names = {pid, pid.lower(), compact}
    removed: list[str] = []

    for root in _local_candidate_roots():
        if not root.is_dir():
            continue
        for name in names:
            target = root / name
            if not target.exists():
                continue
            try:
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink(missing_ok=True)
                removed.append(str(target))
                logger.info("removed local project artifact %s", target)
            except OSError as exc:
                logger.warning("failed to remove local path %s: %s", target, exc)

        # Worker temp dirs sometimes embed the project id in the folder name.
        try:
            for path in root.iterdir():
                if not path.is_dir():
                    continue
                lowered = path.name.lower()
                if compact in lowered or pid.lower() in lowered:
                    if str(path) in removed:
                        continue
                    shutil.rmtree(path, ignore_errors=True)
                    removed.append(str(path))
                    logger.info("removed local scratch match %s", path)
        except OSError as exc:
            logger.warning("failed scanning %s: %s", root, exc)

    return removed


async def purge_r2_project_files(
    storage: R2Storage,
    owner_id: UUID,
    project_id: UUID,
    *,
    extra_keys: list[str] | None = None,
) -> int:
    """Delete every R2 object under the project prefix (+ any leftover keys)."""
    prefix = f"users/{owner_id}/projects/{project_id}/"
    deleted = await storage.delete_prefix(prefix)
    logger.info(
        "purged R2 prefix %s (%s object(s))",
        prefix,
        deleted,
    )

    # Belt-and-suspenders for keys that might sit outside the usual layout.
    for key in extra_keys or []:
        cleaned = (key or "").strip()
        if not cleaned or cleaned.startswith(prefix):
            continue
        try:
            await storage.delete_object(cleaned)
            deleted += 1
            logger.info("deleted extra R2 key %s", cleaned)
        except Exception as exc:  # noqa: BLE001 - continue purge
            logger.warning("failed to delete R2 key %s: %s", cleaned, exc)

    # Abort incomplete multipart uploads under the project prefix.
    try:
        aborted = await storage.abort_multipart_uploads_under_prefix(prefix)
        if aborted:
            logger.info("aborted %s multipart upload(s) under %s", aborted, prefix)
    except Exception as exc:  # noqa: BLE001
        logger.warning("multipart abort under %s failed: %s", prefix, exc)

    return deleted


async def purge_project_artifacts(
    storage: R2Storage,
    owner_id: UUID,
    project_id: UUID,
    project_row: dict | None = None,
) -> dict[str, object]:
    """Remove Cloudflare R2 + local files for a project before/after DB delete."""
    extra_keys: list[str] = []
    if project_row:
        for field in ("source_key", "output_key", "lipsync_output_key"):
            value = project_row.get(field)
            if value:
                extra_keys.append(str(value))

    r2_deleted = 0
    r2_error: str | None = None
    try:
        r2_deleted = await purge_r2_project_files(
            storage, owner_id, project_id, extra_keys=extra_keys
        )
    except Exception as exc:  # noqa: BLE001 - still attempt local + DB cleanup
        r2_error = str(exc)
        logger.exception("R2 purge failed for project %s", project_id)

    local_removed = purge_local_project_files(project_id)
    return {
        "r2_objects_deleted": r2_deleted,
        "r2_error": r2_error,
        "local_removed": local_removed,
    }
