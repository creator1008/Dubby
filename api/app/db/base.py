"""Repository interface shared by all database backends.

Methods return plain dicts (column name -> value) that the routers validate
into response schemas. Every user-facing method takes ``owner_id`` and MUST
scope its queries to that owner — the API layer connects with privileged
credentials, so ownership checks cannot be left to RLS alone.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any
from uuid import UUID

Row = dict[str, Any]


class Repository(ABC):
    # --- lifecycle -----------------------------------------------------------

    async def startup(self) -> None:  # pragma: no cover - trivial default
        """Open pools/clients. Called once from the app lifespan."""

    async def shutdown(self) -> None:  # pragma: no cover - trivial default
        """Release pools/clients."""

    @abstractmethod
    async def ping(self) -> bool:
        """Cheap connectivity check for readiness probes."""

    # --- projects ------------------------------------------------------------

    @abstractmethod
    async def list_projects(self, owner_id: UUID) -> list[Row]: ...

    @abstractmethod
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
    ) -> Row: ...

    @abstractmethod
    async def get_project(self, owner_id: UUID, project_id: UUID) -> Row | None: ...

    @abstractmethod
    async def update_project(
        self, owner_id: UUID, project_id: UUID, fields: dict[str, Any]
    ) -> Row | None:
        """Patch whitelisted columns; returns the updated row or None."""

    @abstractmethod
    async def delete_project(self, owner_id: UUID, project_id: UUID) -> bool: ...

    # --- segments -------------------------------------------------------------

    @abstractmethod
    async def list_segments(self, owner_id: UUID, project_id: UUID) -> list[Row]: ...

    @abstractmethod
    async def update_segment_texts(
        self,
        owner_id: UUID,
        project_id: UUID,
        updates: list[tuple[UUID, str, str | None]],
    ) -> int:
        """Bulk-update ``target_text`` (and optionally ``source_text``);
        returns the number of rows changed."""

    # --- jobs -------------------------------------------------------------------

    @abstractmethod
    async def create_job(
        self,
        owner_id: UUID,
        project_id: UUID,
        kind: str,
        charge_minutes: float = 0,
    ) -> Row:
        """Enqueue a job. Raises ``ActiveJobExistsError`` when a queued or
        running job already exists for the project. Any charge and the job
        insert must happen in the same transaction."""

    @abstractmethod
    async def list_jobs(self, owner_id: UUID, project_id: UUID) -> list[Row]: ...

    @abstractmethod
    async def get_job(self, owner_id: UUID, job_id: UUID) -> Row | None: ...

    # --- worker queue (service-level; no owner scoping) --------------------------

    @abstractmethod
    async def claim_next_job(self) -> Row | None:
        """Atomically claim the oldest queued job (queued -> running)."""

    @abstractmethod
    async def get_job_status(self, job_id: UUID) -> str | None:
        """Current job status; used by the worker to observe cancellation."""

    @abstractmethod
    async def update_job_progress(
        self, job_id: UUID, *, progress: float, message: str | None
    ) -> None: ...

    @abstractmethod
    async def finish_job(
        self,
        job_id: UUID,
        *,
        status: str,
        error: str | None = None,
        progress: float | None = None,
    ) -> None: ...

    @abstractmethod
    async def fail_stale_jobs(self, timeout_seconds: int) -> int:
        """Mark running jobs without a recent heartbeat as failed."""

    # --- worker data access (service-level; no owner scoping) --------------------

    @abstractmethod
    async def get_project_for_worker(self, project_id: UUID) -> Row | None:
        """Project row (including ``owner_id``) fetched by the pipeline."""

    @abstractmethod
    async def update_project_for_worker(
        self, project_id: UUID, fields: dict[str, Any]
    ) -> None:
        """Patch whitelisted project columns (status transitions, output_key)."""

    @abstractmethod
    async def replace_segments(
        self, project_id: UUID, segments: list[Row]
    ) -> int:
        """Atomically replace all segments of a project.

        ``segments`` may also include ``speaker_id`` and ``speaker_overlap``.
        """

    @abstractmethod
    async def list_segments_for_worker(self, project_id: UUID) -> list[Row]:
        """All segments ordered by idx, without owner scoping."""

    # --- credits ------------------------------------------------------------------

    @abstractmethod
    async def get_credit_balance(self, owner_id: UUID) -> float: ...

    @abstractmethod
    async def list_credit_entries(self, owner_id: UUID, limit: int = 50) -> list[Row]: ...

    @abstractmethod
    async def add_credit_entry(
        self,
        owner_id: UUID,
        *,
        delta_minutes: float,
        reason: str,
        project_id: UUID | None = None,
        admin_note: str | None = None,
        adjusted_by: UUID | None = None,
    ) -> Row: ...

    # --- administrator -------------------------------------------------------

    async def admin_list_users(
        self, query: str | None = None, limit: int = 100
    ) -> list[Row]:
        raise NotImplementedError

    async def admin_get_user_usage(self, user_id: UUID) -> Row | None:
        raise NotImplementedError

    async def admin_list_access_logs(self, limit: int = 200) -> list[Row]:
        raise NotImplementedError

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
        raise NotImplementedError

    # --- Stripe billing -------------------------------------------------------

    @abstractmethod
    async def get_stripe_customer(self, owner_id: UUID) -> str | None: ...

    @abstractmethod
    async def get_user_by_stripe_customer(
        self, customer_id: str
    ) -> UUID | None: ...

    @abstractmethod
    async def save_stripe_customer(
        self, owner_id: UUID, customer_id: str
    ) -> str: ...

    @abstractmethod
    async def process_stripe_event(self, event: Row) -> bool:
        """Atomically record and apply a normalized event.

        Returns False if this Stripe event id was already processed.
        """

    # --- RevenueCat billing -------------------------------------------------

    @abstractmethod
    async def process_revenuecat_event(self, event: Row) -> bool:
        """Atomically record a RevenueCat event and mutate billing projections."""


class ActiveJobExistsError(Exception):
    """A queued/running job already exists for the project."""


class InsufficientCreditsError(Exception):
    """Atomic enqueue rejected because the balance was too low."""
