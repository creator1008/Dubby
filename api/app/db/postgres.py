"""asyncpg implementation of the repository interface.

Connects directly to Postgres (Supabase connection pooler in session mode,
or any vanilla Postgres). All queries are parameterized; dynamic UPDATEs are
built only from a hard-coded column whitelist.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg

from ..config import Settings
from .base import (
    ActiveJobExistsError,
    DuplicateVoiceError,
    InsufficientCreditsError,
    Repository,
    Row,
)

_PROJECT_COLUMN_CANDIDATES = (
    "id",
    "title",
    "status",
    "source_lang",
    "target_lang",
    "subtitle_mode",
    "tone_style",
    "diarization_enabled",
    "dub_voice_ids",
    "voice_mode",
    "pipeline_version",
    "duration_seconds",
    "source_key",
    "output_key",
    "lipsync_output_key",
    "quality_warnings",
    "error",
    "created_at",
    "updated_at",
)
# Default SELECT list when information_schema is unavailable.
_PROJECT_COLUMNS = ", ".join(_PROJECT_COLUMN_CANDIDATES)
_JOB_COLUMNS = (
    "id, project_id, kind, status, progress, message, error, created_at, updated_at"
)
_JOB_COLUMNS_J = (
    "j.id, j.project_id, j.kind, j.status, j.progress, j.message, j.error, "
    "j.created_at, j.updated_at"
)
_PROJECT_PATCHABLE = {
    "title",
    "source_lang",
    "target_lang",
    "subtitle_mode",
    "tone_style",
    "diarization_enabled",
    "dub_voice_ids",
    "status",
    "source_key",
    "output_key",
    "lipsync_output_key",
    "quality_warnings",
    "duration_seconds",
    "error",
}

_PROJECT_DELETED_SENTINEL = "__deleted__"


class PostgresRepository(Repository):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: asyncpg.Pool | None = None
        self._project_columns: str = _PROJECT_COLUMNS
        self._project_column_set: set[str] = set(_PROJECT_COLUMN_CANDIDATES)

    async def startup(self) -> None:
        if not self._settings.database_url:
            raise RuntimeError("DATABASE_URL is required for the postgres backend")
        self._pool = await asyncpg.create_pool(
            dsn=self._settings.database_url,
            min_size=self._settings.db_pool_min_size,
            max_size=self._settings.db_pool_max_size,
            command_timeout=30,
        )
        await self._resolve_project_columns()

    async def _resolve_project_columns(self) -> None:
        """Omit columns not yet migrated so list_projects does not 500."""
        try:
            rows = await self.pool.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'projects'"
            )
        except Exception:
            return
        available = {str(r["column_name"]) for r in rows}
        selected = [c for c in _PROJECT_COLUMN_CANDIDATES if c in available]
        if "id" not in available:
            return
        missing = [c for c in _PROJECT_COLUMN_CANDIDATES if c not in available]
        if missing:
            import logging

            logging.getLogger(__name__).warning(
                "projects table missing columns (apply Supabase migrations): %s",
                ", ".join(missing),
            )
        self._project_columns = ", ".join(selected)
        self._project_column_set = set(selected)

    async def shutdown(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("PostgresRepository used before startup()")
        return self._pool

    async def ping(self) -> bool:
        try:
            await self.pool.fetchval("SELECT 1")
            return True
        except Exception:
            return False

    # --- projects -----------------------------------------------------------

    async def list_projects(self, owner_id: UUID) -> list[Row]:
        rows = await self.pool.fetch(
            f"SELECT {self._project_columns} FROM public.projects "
            "WHERE owner_id = $1 "
            "AND coalesce(error, '') <> $2 "
            "ORDER BY created_at DESC",
            owner_id,
            _PROJECT_DELETED_SENTINEL,
        )
        return [dict(r) for r in rows]

    async def create_project(
        self,
        owner_id: UUID,
        *,
        title: str,
        source_lang: str,
        target_lang: str,
        subtitle_mode: str,
        tone_style: str = "neutral",
        diarization_enabled: bool = False,
        dub_voice_ids: list[str] | None = None,
    ) -> Row:
        voices = [
            str(v).strip()
            for v in (dub_voice_ids or [])
            if str(v).strip()
        ][:8]
        cols = [
            "owner_id",
            "title",
            "source_lang",
            "target_lang",
            "subtitle_mode",
            "tone_style",
            "diarization_enabled",
        ]
        values: list[Any] = [
            owner_id,
            title,
            source_lang,
            target_lang,
            subtitle_mode,
            tone_style,
            diarization_enabled,
        ]
        if "dub_voice_ids" in self._project_column_set:
            cols.append("dub_voice_ids")
            values.append(json.dumps(voices))
        if "voice_mode" in self._project_column_set:
            cols.append("voice_mode")
            values.append("voice_box")
        if "pipeline_version" in self._project_column_set:
            cols.append("pipeline_version")
            values.append("2.0")
        placeholders = ", ".join(
            f"${i + 1}::jsonb" if col == "dub_voice_ids" else f"${i + 1}"
            for i, col in enumerate(cols)
        )
        row = await self.pool.fetchrow(
            f"INSERT INTO public.projects ({', '.join(cols)}) "
            f"VALUES ({placeholders}) "
            f"RETURNING {self._project_columns}",
            *values,
        )
        assert row is not None
        return dict(row)

    async def get_project(self, owner_id: UUID, project_id: UUID) -> Row | None:
        row = await self.pool.fetchrow(
            f"SELECT {self._project_columns} FROM public.projects "
            "WHERE owner_id = $1 AND id = $2 "
            "AND coalesce(error, '') <> $3",
            owner_id,
            project_id,
            _PROJECT_DELETED_SENTINEL,
        )
        return dict(row) if row else None

    async def update_project(
        self, owner_id: UUID, project_id: UUID, fields: dict[str, Any]
    ) -> Row | None:
        cols = {k: v for k, v in fields.items() if k in _PROJECT_PATCHABLE}
        if isinstance(cols.get("quality_warnings"), list):
            cols["quality_warnings"] = json.dumps(cols["quality_warnings"])
        if isinstance(cols.get("dub_voice_ids"), list):
            cols["dub_voice_ids"] = json.dumps(
                [str(v).strip() for v in cols["dub_voice_ids"] if str(v).strip()][:8]
            )
        if not cols:
            return await self.get_project(owner_id, project_id)
        sets = ", ".join(f"{col} = ${i + 3}" for i, col in enumerate(cols))
        row = await self.pool.fetchrow(
            f"UPDATE public.projects SET {sets} "
            "WHERE owner_id = $1 AND id = $2 "
            f"RETURNING {self._project_columns}",
            owner_id,
            project_id,
            *cols.values(),
        )
        return dict(row) if row else None

    async def delete_project(self, owner_id: UUID, project_id: UUID) -> bool:
        """Hard-delete when possible; soft-delete when credit_ledger blocks FK nulling."""
        try:
            result = await self.pool.execute(
                "DELETE FROM public.projects WHERE owner_id = $1 AND id = $2",
                owner_id,
                project_id,
            )
            if result.endswith("1"):
                return True
        except Exception as exc:
            message = str(exc)
            if "credit_ledger_is_immutable" not in message:
                raise
            # Append-only ledger blocks ON DELETE SET NULL — hide the project
            # and purge child media rows so it disappears from history.
            await self._purge_project_children(project_id)
            sets = [
                "error = $3",
                "status = 'failed'",
                "source_key = NULL",
                "output_key = NULL",
                "duration_seconds = NULL",
            ]
            if "lipsync_output_key" in self._project_column_set:
                sets.append("lipsync_output_key = NULL")
            if "quality_warnings" in self._project_column_set:
                sets.append("quality_warnings = '[]'::jsonb")
            soft = await self.pool.fetchrow(
                f"UPDATE public.projects SET {', '.join(sets)} "
                "WHERE owner_id = $1 AND id = $2 "
                f"RETURNING {self._project_columns}",
                owner_id,
                project_id,
                _PROJECT_DELETED_SENTINEL,
            )
            return soft is not None

        # Already soft-deleted or missing.
        existing = await self.pool.fetchrow(
            "SELECT id, error FROM public.projects "
            "WHERE owner_id = $1 AND id = $2",
            owner_id,
            project_id,
        )
        return bool(existing) and existing["error"] == _PROJECT_DELETED_SENTINEL

    async def _purge_project_children(self, project_id: UUID) -> None:
        """Best-effort delete of segments/jobs when the project row must remain."""
        for table in ("segments", "jobs"):
            try:
                await self.pool.execute(
                    f"DELETE FROM public.{table} WHERE project_id = $1",
                    project_id,
                )
            except Exception:
                pass

    # --- segments -------------------------------------------------------------

    async def list_segments(self, owner_id: UUID, project_id: UUID) -> list[Row]:
        rows = await self.pool.fetch(
            "SELECT s.id, s.project_id, s.idx, s.start_ms, s.end_ms, "
            "s.source_text, s.target_text, s.speaker_id, s.speaker_overlap, "
            "s.speak_speed "
            "FROM public.segments s "
            "JOIN public.projects p ON p.id = s.project_id "
            "WHERE p.owner_id = $1 AND s.project_id = $2 "
            "ORDER BY s.idx",
            owner_id,
            project_id,
        )
        return [dict(r) for r in rows]

    async def update_segment_texts(
        self,
        owner_id: UUID,
        project_id: UUID,
        updates: list[tuple[UUID, str, str | None, int | None, float | None]],
    ) -> int:
        payload = json.dumps(
            [
                {
                    "id": str(seg_id),
                    "target_text": target,
                    "source_text": source,
                    **({"end_ms": end_ms} if end_ms is not None else {}),
                    **(
                        {"speak_speed": speak_speed}
                        if speak_speed is not None
                        else {}
                    ),
                }
                for seg_id, target, source, end_ms, speak_speed in updates
            ]
        )
        count = await self.pool.fetchval(
            """
            WITH input AS (
                SELECT (elem->>'id')::uuid AS id,
                       elem->>'target_text' AS target_text,
                       elem->>'source_text' AS source_text,
                       CASE
                         WHEN elem ? 'end_ms'
                              AND nullif(elem->>'end_ms', '') IS NOT NULL
                         THEN (elem->>'end_ms')::integer
                         ELSE NULL
                       END AS end_ms,
                       CASE
                         WHEN elem ? 'speak_speed'
                              AND nullif(elem->>'speak_speed', '') IS NOT NULL
                         THEN (elem->>'speak_speed')::double precision
                         ELSE NULL
                       END AS speak_speed
                FROM jsonb_array_elements($3::jsonb) AS elem
            ),
            updated AS (
                UPDATE public.segments s
                SET target_text = input.target_text,
                    source_text = coalesce(input.source_text, s.source_text),
                    end_ms = CASE
                      WHEN input.end_ms IS NOT NULL AND input.end_ms > s.start_ms
                      THEN input.end_ms
                      ELSE s.end_ms
                    END,
                    speak_speed = coalesce(input.speak_speed, s.speak_speed)
                FROM input, public.projects p
                WHERE s.id = input.id
                  AND s.project_id = $2
                  AND p.id = s.project_id
                  AND p.owner_id = $1
                RETURNING s.id
            )
            SELECT count(*) FROM updated
            """,
            owner_id,
            project_id,
            payload,
        )
        return int(count or 0)

    # --- jobs -------------------------------------------------------------------

    async def create_job(
        self,
        owner_id: UUID,
        project_id: UUID,
        kind: str,
        charge_minutes: float = 0,
    ) -> Row:
        async with self.pool.acquire() as conn, conn.transaction():
            owned = await conn.fetchval(
                "SELECT 1 FROM public.projects "
                "WHERE owner_id = $1 AND id = $2 FOR UPDATE",
                owner_id,
                project_id,
            )
            if not owned:
                raise LookupError("project not found")
            active = await conn.fetchval(
                "SELECT 1 FROM public.jobs WHERE project_id = $1 "
                "AND status IN ('queued', 'running')",
                project_id,
            )
            if active:
                raise ActiveJobExistsError
            if charge_minutes:
                await conn.fetchval(
                    "SELECT 1 FROM public.profiles WHERE id = $1 FOR UPDATE",
                    owner_id,
                )
                balance = await conn.fetchval(
                    "SELECT COALESCE(sum(delta_minutes), 0) "
                    "FROM public.credit_ledger WHERE user_id = $1",
                    owner_id,
                )
                if float(balance or 0) < charge_minutes:
                    raise InsufficientCreditsError
            row = await conn.fetchrow(
                "INSERT INTO public.jobs (project_id, kind, charged_minutes) "
                "VALUES ($1, $2, $3) "
                f"RETURNING {_JOB_COLUMNS}",
                project_id,
                kind,
                charge_minutes,
            )
            assert row is not None
            if charge_minutes:
                await conn.execute(
                    "INSERT INTO public.credit_ledger "
                    "(user_id, delta_minutes, reason, project_id, job_id, idempotency_key) "
                    "VALUES ($1, $2, $3, $4, $5, $6)",
                    owner_id,
                    -charge_minutes,
                    "lipsync_job" if kind == "lipsync" else "dub_job",
                    project_id,
                    row["id"],
                    f"job:{row['id']}:debit",
                )
            return dict(row)

    async def list_jobs(self, owner_id: UUID, project_id: UUID) -> list[Row]:
        rows = await self.pool.fetch(
            f"SELECT {_JOB_COLUMNS_J} "
            "FROM public.jobs j "
            "JOIN public.projects p ON p.id = j.project_id "
            "WHERE p.owner_id = $1 AND j.project_id = $2 "
            "ORDER BY j.created_at DESC",
            owner_id,
            project_id,
        )
        return [dict(r) for r in rows]

    async def get_job(self, owner_id: UUID, job_id: UUID) -> Row | None:
        row = await self.pool.fetchrow(
            f"SELECT {_JOB_COLUMNS_J} "
            "FROM public.jobs j "
            "JOIN public.projects p ON p.id = j.project_id "
            "WHERE p.owner_id = $1 AND j.id = $2",
            owner_id,
            job_id,
        )
        return dict(row) if row else None

    # --- worker queue -------------------------------------------------------------

    async def claim_next_job(self) -> Row | None:
        row = await self.pool.fetchrow(
            """
            UPDATE public.jobs
            SET status = 'running', started_at = now(), heartbeat_at = now()
            WHERE id = (
                SELECT id FROM public.jobs
                WHERE status = 'queued'
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING id, project_id, kind, status, progress, message, error,
                      created_at, updated_at
            """
        )
        return dict(row) if row else None

    async def get_job_status(self, job_id: UUID) -> str | None:
        value = await self.pool.fetchval(
            "SELECT status FROM public.jobs WHERE id = $1", job_id
        )
        return str(value) if value is not None else None

    async def update_job_progress(
        self, job_id: UUID, *, progress: float, message: str | None
    ) -> None:
        await self.pool.execute(
            "UPDATE public.jobs "
            "SET progress = $2, message = $3, heartbeat_at = now() "
            "WHERE id = $1 AND status = 'running'",
            job_id,
            progress,
            message,
        )

    async def finish_job(
        self,
        job_id: UUID,
        *,
        status: str,
        error: str | None = None,
        progress: float | None = None,
    ) -> None:
        await self.pool.execute(
            "SELECT public.finish_job_with_refund($1, $2, $3, $4)",
            job_id,
            status,
            error,
            progress,
        )

    async def fail_stale_jobs(self, timeout_seconds: int) -> int:
        rows = await self.pool.fetch(
            "SELECT id FROM public.jobs WHERE status = 'running' "
            "AND heartbeat_at < now() - make_interval(secs => $1)",
            float(timeout_seconds),
        )
        for row in rows:
            await self.finish_job(
                row["id"], status="failed", error="worker timeout"
            )
        return len(rows)

    # --- worker data access -----------------------------------------------------

    async def get_project_for_worker(self, project_id: UUID) -> Row | None:
        row = await self.pool.fetchrow(
            f"SELECT owner_id, {self._project_columns} FROM public.projects WHERE id = $1",
            project_id,
        )
        return dict(row) if row else None

    async def update_project_for_worker(
        self, project_id: UUID, fields: dict[str, Any]
    ) -> None:
        cols = {k: v for k, v in fields.items() if k in _PROJECT_PATCHABLE}
        if isinstance(cols.get("quality_warnings"), list):
            cols["quality_warnings"] = json.dumps(cols["quality_warnings"])
        if not cols:
            return
        sets = ", ".join(f"{col} = ${i + 2}" for i, col in enumerate(cols))
        await self.pool.execute(
            f"UPDATE public.projects SET {sets} WHERE id = $1",
            project_id,
            *cols.values(),
        )

    async def replace_segments(self, project_id: UUID, segments: list[Row]) -> int:
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "DELETE FROM public.segments WHERE project_id = $1", project_id
            )
            if not segments:
                return 0
            await conn.executemany(
                "INSERT INTO public.segments "
                "(project_id, idx, start_ms, end_ms, source_text, target_text, "
                "speaker_id, speaker_overlap) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                [
                    (
                        project_id,
                        int(s["idx"]),
                        int(s["start_ms"]),
                        int(s["end_ms"]),
                        str(s.get("source_text", "")),
                        str(s.get("target_text", "")),
                        s.get("speaker_id"),
                        bool(s.get("speaker_overlap", False)),
                    )
                    for s in segments
                ],
            )
            return len(segments)

    async def list_segments_for_worker(self, project_id: UUID) -> list[Row]:
        rows = await self.pool.fetch(
            "SELECT id, project_id, idx, start_ms, end_ms, source_text, target_text, "
            "speaker_id, speaker_overlap, speak_speed "
            "FROM public.segments WHERE project_id = $1 ORDER BY idx",
            project_id,
        )
        return [dict(r) for r in rows]

    # --- credits --------------------------------------------------------------------

    async def get_credit_balance(self, owner_id: UUID) -> float:
        value = await self.pool.fetchval(
            "SELECT COALESCE(sum(delta_minutes), 0) FROM public.credit_ledger "
            "WHERE user_id = $1",
            owner_id,
        )
        return float(value or 0)

    async def list_credit_entries(self, owner_id: UUID, limit: int = 50) -> list[Row]:
        rows = await self.pool.fetch(
            "SELECT id, delta_minutes, reason, project_id, created_at "
            "FROM public.credit_ledger "
            "WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2",
            owner_id,
            limit,
        )
        return [dict(r) for r in rows]

    async def add_credit_entry(
        self,
        owner_id: UUID,
        *,
        delta_minutes: float,
        reason: str,
        project_id: UUID | None = None,
        admin_note: str | None = None,
        adjusted_by: UUID | None = None,
    ) -> Row:
        row = await self.pool.fetchrow(
            "INSERT INTO public.credit_ledger "
            "(user_id,delta_minutes,reason,project_id,admin_note,adjusted_by) "
            "VALUES ($1,$2,$3,$4,$5,$6) "
            "RETURNING id, delta_minutes, reason, project_id, created_at",
            owner_id,
            delta_minutes,
            reason,
            project_id,
            admin_note,
            adjusted_by,
        )
        assert row is not None
        return dict(row)

    # --- administrator ------------------------------------------------------

    async def is_user_active(self, user_id: UUID) -> bool:
        row = await self.pool.fetchrow(
            "SELECT coalesce(is_active, true) AS is_active "
            "FROM public.profiles WHERE id=$1",
            user_id,
        )
        if row is None:
            return True
        return bool(row["is_active"])

    async def admin_list_users(
        self, query: str | None = None, limit: int = 100
    ) -> list[Row]:
        needle = (query or "").strip()
        rows = await self.pool.fetch(
            """
            SELECT p.id, p.email, p.display_name, p.country, p.auth_provider,
                   p.created_at, p.last_login_at,
                   coalesce(p.is_active, true) AS is_active,
                   p.deactivated_at,
                   (SELECT count(*)::integer FROM public.projects pr
                     WHERE pr.owner_id = p.id) AS project_count,
                   (SELECT coalesce(sum(cl.delta_minutes), 0)
                      FROM public.credit_ledger cl
                     WHERE cl.user_id = p.id) AS credit_balance
              FROM public.profiles p
             WHERE $1 = ''
                OR p.email ILIKE '%' || $1 || '%'
                OR p.display_name ILIKE '%' || $1 || '%'
             ORDER BY p.created_at DESC
             LIMIT $2
            """,
            needle,
            limit,
        )
        return [dict(row) for row in rows]

    async def admin_get_user_usage(self, user_id: UUID) -> Row | None:
        profile = await self.pool.fetchrow(
            "SELECT id,email,display_name,country,auth_provider,created_at,"
            "last_login_at,coalesce(is_active,true) AS is_active,deactivated_at "
            "FROM public.profiles WHERE id=$1",
            user_id,
        )
        if profile is None:
            return None
        projects = await self.pool.fetch(
            "SELECT id,title,status,source_lang,target_lang,duration_seconds,created_at "
            "FROM public.projects WHERE owner_id=$1 "
            "AND coalesce(error, '') <> '__deleted__' "
            "AND status = 'completed' "
            "ORDER BY created_at DESC LIMIT 100",
            user_id,
        )
        jobs = await self.pool.fetch(
            """
            SELECT j.id, j.kind, j.status, j.progress, j.charged_minutes,
                   j.error, j.created_at, j.started_at, j.finished_at,
                   p.id AS project_id, p.title AS project_title
              FROM public.jobs j
              JOIN public.projects p ON p.id = j.project_id
             WHERE p.owner_id = $1
             ORDER BY j.created_at DESC
             LIMIT 200
            """,
            user_id,
        )
        credits = await self.pool.fetch(
            "SELECT id,delta_minutes,reason,project_id,job_id,admin_note,"
            "adjusted_by,created_at "
            "FROM public.credit_ledger WHERE user_id=$1 "
            "ORDER BY created_at DESC LIMIT 200",
            user_id,
        )
        purchases = await self.pool.fetch(
            "SELECT id,delta_minutes,reason,external_reference,created_at "
            "FROM public.credit_ledger WHERE user_id=$1 AND reason='purchase' "
            "ORDER BY created_at DESC LIMIT 100",
            user_id,
        )
        subscriptions = await self.pool.fetch(
            "SELECT stripe_subscription_id,stripe_customer_id,status,price_id,"
            "current_period_end,cancel_at_period_end,created_at,updated_at "
            "FROM public.stripe_subscriptions WHERE user_id=$1 "
            "ORDER BY updated_at DESC LIMIT 50",
            user_id,
        )
        balance = await self.get_credit_balance(user_id)
        return {
            "profile": dict(profile),
            "projects": [dict(row) for row in projects],
            "jobs": [dict(row) for row in jobs],
            "credits": [dict(row) for row in credits],
            "payments": {
                "purchases": [dict(row) for row in purchases],
                "subscriptions": [dict(row) for row in subscriptions],
            },
            "credit_balance": balance,
        }

    async def admin_set_user_active(
        self, user_id: UUID, *, is_active: bool
    ) -> Row | None:
        row = await self.pool.fetchrow(
            """
            UPDATE public.profiles
               SET is_active = $2,
                   deactivated_at = CASE WHEN $2 THEN NULL ELSE now() END,
                   updated_at = now()
             WHERE id = $1
         RETURNING id,email,display_name,country,auth_provider,created_at,
                   last_login_at,is_active,deactivated_at
            """,
            user_id,
            is_active,
        )
        return dict(row) if row else None

    async def admin_list_access_logs(self, limit: int = 200) -> list[Row]:
        rows = await self.pool.fetch(
            "SELECT l.id,l.user_id,p.email,l.method,l.path,l.status_code,"
            "l.ip_address::text AS ip_address,l.user_agent,l.created_at "
            "FROM public.access_logs l LEFT JOIN public.profiles p ON p.id=l.user_id "
            "ORDER BY l.created_at DESC LIMIT $1",
            limit,
        )
        return [dict(row) for row in rows]

    async def record_access_log(
        self,
        user_id: UUID | None,
        *,
        method: str,
        path: str,
        status_code: int,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        await self.pool.execute(
            "INSERT INTO public.access_logs "
            "(user_id,method,path,status_code,ip_address,user_agent) "
            "VALUES ($1,$2,$3,$4,$5::inet,$6)",
            user_id,
            method[:16],
            path[:500],
            status_code,
            ip_address,
            (user_agent or "")[:500],
        )

    # --- Stripe billing ------------------------------------------------------

    async def get_stripe_customer(self, owner_id: UUID) -> str | None:
        value = await self.pool.fetchval(
            "SELECT stripe_customer_id FROM public.stripe_customers "
            "WHERE user_id = $1",
            owner_id,
        )
        return str(value) if value else None

    async def get_user_by_stripe_customer(
        self, customer_id: str
    ) -> UUID | None:
        value = await self.pool.fetchval(
            "SELECT user_id FROM public.stripe_customers "
            "WHERE stripe_customer_id = $1",
            customer_id,
        )
        return UUID(str(value)) if value else None

    async def save_stripe_customer(
        self, owner_id: UUID, customer_id: str
    ) -> str:
        return str(
            await self.pool.fetchval(
                "INSERT INTO public.stripe_customers (user_id, stripe_customer_id) "
                "VALUES ($1, $2) ON CONFLICT (user_id) DO UPDATE "
                "SET stripe_customer_id = public.stripe_customers.stripe_customer_id "
                "RETURNING stripe_customer_id",
                owner_id,
                customer_id,
            )
        )

    async def process_stripe_event(self, event: Row) -> bool:
        async with self.pool.acquire() as conn, conn.transaction():
            inserted = await conn.fetchval(
                "INSERT INTO public.stripe_events "
                "(stripe_event_id, event_type, payload) VALUES ($1, $2, $3::jsonb) "
                "ON CONFLICT DO NOTHING RETURNING stripe_event_id",
                event["event_id"],
                event["event_type"],
                json.dumps(event["payload"]),
            )
            if not inserted:
                return False

            user_id = event.get("user_id")
            customer_id = event.get("customer_id")
            if user_id and customer_id:
                await conn.execute(
                    "INSERT INTO public.stripe_customers "
                    "(user_id, stripe_customer_id) VALUES ($1, $2) "
                    "ON CONFLICT (user_id) DO UPDATE SET stripe_customer_id = EXCLUDED.stripe_customer_id",
                    user_id,
                    customer_id,
                )

            subscription_id = event.get("subscription_id")
            if subscription_id and user_id:
                await conn.execute(
                    "INSERT INTO public.stripe_subscriptions "
                    "(stripe_subscription_id, user_id, stripe_customer_id, status, "
                    "price_id, current_period_end, cancel_at_period_end) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7) "
                    "ON CONFLICT (stripe_subscription_id) DO UPDATE SET "
                    "status=EXCLUDED.status, price_id=EXCLUDED.price_id, "
                    "current_period_end=EXCLUDED.current_period_end, "
                    "cancel_at_period_end=EXCLUDED.cancel_at_period_end",
                    subscription_id,
                    user_id,
                    customer_id or "",
                    event.get("subscription_status") or "unknown",
                    event.get("price_id"),
                    event.get("period_end"),
                    bool(event.get("cancel_at_period_end")),
                )

            minutes = float(event.get("credit_minutes") or 0)
            reference = event.get("credit_reference")
            if minutes > 0 and user_id and reference:
                await conn.fetchval(
                    "SELECT 1 FROM public.profiles WHERE id = $1 FOR UPDATE", user_id
                )
                await conn.execute(
                    "INSERT INTO public.credit_ledger "
                    "(user_id, delta_minutes, reason, external_reference, idempotency_key) "
                    "VALUES ($1,$2,'purchase',$3,$4) "
                    "ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING",
                    user_id,
                    minutes,
                    reference,
                    f"stripe-credit:{reference}",
                )
            return True

    async def process_revenuecat_event(self, event: Row) -> bool:
        result = await self.pool.fetchval(
            "SELECT public.process_revenuecat_event("
            "$1,$2,$3::jsonb,$4,$5,$6,$7::jsonb,$8,$9,$10,$11,$12,$13)",
            event["event_id"],
            event["event_type"],
            json.dumps(event["payload"]),
            event.get("user_id"),
            event.get("app_user_id"),
            event.get("product_id"),
            json.dumps(event.get("entitlement_ids") or []),
            event.get("transaction_id"),
            event.get("original_transaction_id"),
            event.get("status"),
            event.get("expires_at"),
            event.get("store"),
            float(event.get("credit_minutes") or 0),
        )
        return bool(result)

    # --- voice box ----------------------------------------------------------

    _USER_VOICE_COLUMNS = (
        "id, owner_id, nickname, elevenlabs_voice_id, shared_voice_id, "
        "public_owner_id, name, description, gender, accent, category, "
        "language, age, preview_url, created_at"
    )

    async def list_user_voices(self, owner_id: UUID) -> list[Row]:
        rows = await self.pool.fetch(
            f"SELECT {self._USER_VOICE_COLUMNS} FROM public.user_voices "
            "WHERE owner_id = $1 ORDER BY created_at DESC",
            owner_id,
        )
        return [dict(row) for row in rows]

    async def get_user_voice(self, owner_id: UUID, voice_row_id: UUID) -> Row | None:
        row = await self.pool.fetchrow(
            f"SELECT {self._USER_VOICE_COLUMNS} FROM public.user_voices "
            "WHERE owner_id = $1 AND id = $2",
            owner_id,
            voice_row_id,
        )
        return dict(row) if row else None

    async def add_user_voice(self, owner_id: UUID, fields: dict[str, Any]) -> Row:
        try:
            voice_id = fields.get("id")
            if voice_id is not None:
                row = await self.pool.fetchrow(
                    "INSERT INTO public.user_voices ("
                    "id, owner_id, nickname, elevenlabs_voice_id, shared_voice_id, "
                    "public_owner_id, name, description, gender, accent, category, "
                    "language, age, preview_url"
                    ") VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14) "
                    f"RETURNING {self._USER_VOICE_COLUMNS}",
                    voice_id,
                    owner_id,
                    fields["nickname"],
                    fields["elevenlabs_voice_id"],
                    fields["shared_voice_id"],
                    fields.get("public_owner_id") or "",
                    fields.get("name") or "",
                    fields.get("description") or "",
                    fields.get("gender") or "",
                    fields.get("accent") or "",
                    fields.get("category") or "",
                    fields.get("language") or "",
                    fields.get("age") or "",
                    fields.get("preview_url"),
                )
            else:
                row = await self.pool.fetchrow(
                    "INSERT INTO public.user_voices ("
                    "owner_id, nickname, elevenlabs_voice_id, shared_voice_id, "
                    "public_owner_id, name, description, gender, accent, category, "
                    "language, age, preview_url"
                    ") VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13) "
                    f"RETURNING {self._USER_VOICE_COLUMNS}",
                    owner_id,
                    fields["nickname"],
                    fields["elevenlabs_voice_id"],
                    fields["shared_voice_id"],
                    fields.get("public_owner_id") or "",
                    fields.get("name") or "",
                    fields.get("description") or "",
                    fields.get("gender") or "",
                    fields.get("accent") or "",
                    fields.get("category") or "",
                    fields.get("language") or "",
                    fields.get("age") or "",
                    fields.get("preview_url"),
                )
        except asyncpg.UniqueViolationError as exc:
            raise DuplicateVoiceError("voice already saved or nickname taken") from exc
        return dict(row)

    async def delete_user_voice(self, owner_id: UUID, voice_row_id: UUID) -> bool:
        result = await self.pool.execute(
            "DELETE FROM public.user_voices WHERE owner_id = $1 AND id = $2",
            owner_id,
            voice_row_id,
        )
        return result.endswith("1")

