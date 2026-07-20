"""Supabase PostgREST implementation of the repository interface.

Uses the service-role key over HTTPS, so it works from environments without
direct Postgres connectivity. Operations that must be atomic (job enqueueing,
queue claiming, bulk segment updates) are delegated to SQL functions created
by the Supabase migration and invoked through ``/rest/v1/rpc``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx

from ..config import Settings
from .base import (
    ActiveJobExistsError,
    InsufficientCreditsError,
    Repository,
    Row,
)

_PROJECT_SELECT = (
    "id,title,status,source_lang,target_lang,subtitle_mode,tone_style,"
    "diarization_enabled,duration_seconds,source_key,output_key,"
    "lipsync_output_key,quality_warnings,error,created_at,updated_at"
)
_JOB_SELECT = "id,project_id,kind,status,progress,message,error,created_at,updated_at"
_PROJECT_PATCHABLE = {
    "title",
    "source_lang",
    "target_lang",
    "subtitle_mode",
    "tone_style",
    "diarization_enabled",
    "status",
    "source_key",
    "output_key",
    "lipsync_output_key",
    "quality_warnings",
    "duration_seconds",
    "error",
}


class SupabaseRestRepository(Repository):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: httpx.AsyncClient | None = None

    async def startup(self) -> None:
        if not (self._settings.supabase_url and self._settings.supabase_service_role_key):
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for the "
                "supabase_rest backend"
            )
        key = self._settings.supabase_service_role_key
        self._client = httpx.AsyncClient(
            base_url=f"{self._settings.supabase_url.rstrip('/')}/rest/v1",
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(15.0),
        )

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("SupabaseRestRepository used before startup()")
        return self._client

    async def _rpc(self, name: str, payload: dict[str, Any]) -> Any:
        resp = await self.client.post(f"/rpc/{name}", json=payload)
        resp.raise_for_status()
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    async def ping(self) -> bool:
        try:
            resp = await self.client.get(
                "/projects", params={"select": "id", "limit": "1"}
            )
            return resp.status_code < 500
        except httpx.HTTPError:
            return False

    # --- projects -----------------------------------------------------------

    async def list_projects(self, owner_id: UUID) -> list[Row]:
        resp = await self.client.get(
            "/projects",
            params={
                "select": _PROJECT_SELECT,
                "owner_id": f"eq.{owner_id}",
                "order": "created_at.desc",
            },
        )
        resp.raise_for_status()
        return resp.json()

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
    ) -> Row:
        resp = await self.client.post(
            "/projects",
            params={"select": _PROJECT_SELECT},
            headers={"Prefer": "return=representation"},
            json={
                "owner_id": str(owner_id),
                "title": title,
                "source_lang": source_lang,
                "target_lang": target_lang,
                "subtitle_mode": subtitle_mode,
                "tone_style": tone_style,
                "diarization_enabled": diarization_enabled,
            },
        )
        resp.raise_for_status()
        return resp.json()[0]

    async def get_project(self, owner_id: UUID, project_id: UUID) -> Row | None:
        resp = await self.client.get(
            "/projects",
            params={
                "select": _PROJECT_SELECT,
                "owner_id": f"eq.{owner_id}",
                "id": f"eq.{project_id}",
                "limit": "1",
            },
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else None

    async def update_project(
        self, owner_id: UUID, project_id: UUID, fields: dict[str, Any]
    ) -> Row | None:
        payload = {k: v for k, v in fields.items() if k in _PROJECT_PATCHABLE}
        if not payload:
            return await self.get_project(owner_id, project_id)
        resp = await self.client.patch(
            "/projects",
            params={
                "select": _PROJECT_SELECT,
                "owner_id": f"eq.{owner_id}",
                "id": f"eq.{project_id}",
            },
            headers={"Prefer": "return=representation"},
            json=payload,
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else None

    async def delete_project(self, owner_id: UUID, project_id: UUID) -> bool:
        resp = await self.client.delete(
            "/projects",
            params={"owner_id": f"eq.{owner_id}", "id": f"eq.{project_id}"},
            headers={"Prefer": "return=representation"},
        )
        resp.raise_for_status()
        return bool(resp.json())

    # --- segments -------------------------------------------------------------

    async def list_segments(self, owner_id: UUID, project_id: UUID) -> list[Row]:
        # Ownership check first: segments has no owner_id column.
        if await self.get_project(owner_id, project_id) is None:
            return []
        resp = await self.client.get(
            "/segments",
            params={
                "select": "id,project_id,idx,start_ms,end_ms,source_text,target_text,"
                "speaker_id,speaker_overlap",
                "project_id": f"eq.{project_id}",
                "order": "idx.asc",
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def update_segment_texts(
        self,
        owner_id: UUID,
        project_id: UUID,
        updates: list[tuple[UUID, str, str | None]],
    ) -> int:
        result = await self._rpc(
            "update_segment_texts",
            {
                "p_owner_id": str(owner_id),
                "p_project_id": str(project_id),
                "p_updates": [
                    {
                        "id": str(seg_id),
                        "target_text": target,
                        "source_text": source,
                    }
                    for seg_id, target, source in updates
                ],
            },
        )
        return int(result or 0)

    # --- jobs -------------------------------------------------------------------

    async def create_job(
        self,
        owner_id: UUID,
        project_id: UUID,
        kind: str,
        charge_minutes: float = 0,
    ) -> Row:
        try:
            result = await self._rpc(
                "enqueue_job_with_credit",
                {
                    "p_owner_id": str(owner_id),
                    "p_project_id": str(project_id),
                    "p_kind": kind,
                    "p_charge_minutes": charge_minutes,
                },
            )
        except httpx.HTTPStatusError as exc:
            body = exc.response.text
            if "active_job_exists" in body:
                raise ActiveJobExistsError from exc
            if "project_not_found" in body:
                raise LookupError("project not found") from exc
            if "insufficient_credits" in body:
                raise InsufficientCreditsError from exc
            raise
        rows = result if isinstance(result, list) else [result]
        return rows[0]

    async def list_jobs(self, owner_id: UUID, project_id: UUID) -> list[Row]:
        if await self.get_project(owner_id, project_id) is None:
            return []
        resp = await self.client.get(
            "/jobs",
            params={
                "select": _JOB_SELECT,
                "project_id": f"eq.{project_id}",
                "order": "created_at.desc",
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def get_job(self, owner_id: UUID, job_id: UUID) -> Row | None:
        resp = await self.client.get(
            "/jobs",
            params={
                "select": _JOB_SELECT + ",projects!inner(owner_id)",
                "id": f"eq.{job_id}",
                "projects.owner_id": f"eq.{owner_id}",
                "limit": "1",
            },
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return None
        row = rows[0]
        row.pop("projects", None)
        return row

    # --- worker queue -------------------------------------------------------------

    async def claim_next_job(self) -> Row | None:
        result = await self._rpc("claim_next_job", {})
        if not result:
            return None
        rows = result if isinstance(result, list) else [result]
        return rows[0] if rows else None

    async def get_job_status(self, job_id: UUID) -> str | None:
        resp = await self.client.get(
            "/jobs",
            params={"select": "status", "id": f"eq.{job_id}", "limit": "1"},
        )
        resp.raise_for_status()
        rows = resp.json()
        return str(rows[0]["status"]) if rows else None

    async def update_job_progress(
        self, job_id: UUID, *, progress: float, message: str | None
    ) -> None:
        resp = await self.client.patch(
            "/jobs",
            params={"id": f"eq.{job_id}", "status": "eq.running"},
            json={
                "progress": progress,
                "message": message,
                "heartbeat_at": "now",
            },
        )
        resp.raise_for_status()

    async def finish_job(
        self,
        job_id: UUID,
        *,
        status: str,
        error: str | None = None,
        progress: float | None = None,
    ) -> None:
        await self._rpc(
            "finish_job_with_refund",
            {
                "p_job_id": str(job_id),
                "p_status": status,
                "p_error": error,
                "p_progress": progress,
            },
        )

    async def fail_stale_jobs(self, timeout_seconds: int) -> int:
        result = await self._rpc(
            "fail_stale_jobs", {"p_timeout_seconds": timeout_seconds}
        )
        return int(result or 0)

    # --- worker data access -----------------------------------------------------

    async def get_project_for_worker(self, project_id: UUID) -> Row | None:
        resp = await self.client.get(
            "/projects",
            params={
                "select": "owner_id," + _PROJECT_SELECT,
                "id": f"eq.{project_id}",
                "limit": "1",
            },
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else None

    async def update_project_for_worker(
        self, project_id: UUID, fields: dict[str, Any]
    ) -> None:
        payload = {k: v for k, v in fields.items() if k in _PROJECT_PATCHABLE}
        if not payload:
            return
        resp = await self.client.patch(
            "/projects", params={"id": f"eq.{project_id}"}, json=payload
        )
        resp.raise_for_status()

    async def replace_segments(self, project_id: UUID, segments: list[Row]) -> int:
        # Delegated to a SECURITY DEFINER SQL function so delete+insert is atomic.
        result = await self._rpc(
            "replace_segments",
            {
                "p_project_id": str(project_id),
                "p_segments": [
                    {
                        "idx": int(s["idx"]),
                        "start_ms": int(s["start_ms"]),
                        "end_ms": int(s["end_ms"]),
                        "source_text": str(s.get("source_text", "")),
                        "target_text": str(s.get("target_text", "")),
                        "speaker_id": s.get("speaker_id"),
                        "speaker_overlap": bool(s.get("speaker_overlap", False)),
                    }
                    for s in segments
                ],
            },
        )
        return int(result or 0)

    async def list_segments_for_worker(self, project_id: UUID) -> list[Row]:
        resp = await self.client.get(
            "/segments",
            params={
                "select": "id,project_id,idx,start_ms,end_ms,source_text,target_text,"
                "speaker_id,speaker_overlap",
                "project_id": f"eq.{project_id}",
                "order": "idx.asc",
            },
        )
        resp.raise_for_status()
        return resp.json()

    # --- credits --------------------------------------------------------------------

    async def get_credit_balance(self, owner_id: UUID) -> float:
        result = await self._rpc("credit_balance", {"p_user_id": str(owner_id)})
        return float(result or 0)

    async def list_credit_entries(self, owner_id: UUID, limit: int = 50) -> list[Row]:
        resp = await self.client.get(
            "/credit_ledger",
            params={
                "select": "id,delta_minutes,reason,project_id,created_at",
                "user_id": f"eq.{owner_id}",
                "order": "created_at.desc",
                "limit": str(limit),
            },
        )
        resp.raise_for_status()
        return resp.json()

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
        resp = await self.client.post(
            "/credit_ledger",
            params={"select": "id,delta_minutes,reason,project_id,created_at"},
            headers={"Prefer": "return=representation"},
            json={
                "user_id": str(owner_id),
                "delta_minutes": delta_minutes,
                "reason": reason,
                "project_id": str(project_id) if project_id else None,
                "admin_note": admin_note,
                "adjusted_by": str(adjusted_by) if adjusted_by else None,
            },
        )
        resp.raise_for_status()
        return resp.json()[0]

    # --- administrator ------------------------------------------------------

    async def admin_list_users(
        self, query: str | None = None, limit: int = 100
    ) -> list[Row]:
        resp = await self.client.get(
            "/profiles",
            params={
                "select": (
                    "id,email,display_name,country,auth_provider,"
                    "created_at,last_login_at"
                ),
                "order": "created_at.desc",
                "limit": str(limit),
            },
        )
        resp.raise_for_status()
        needle = (query or "").strip().lower()
        rows = [
            row
            for row in resp.json()
            if not needle
            or needle in str(row.get("email") or "").lower()
            or needle in str(row.get("display_name") or "").lower()
        ]
        for row in rows:
            user_id = UUID(str(row["id"]))
            row["credit_balance"] = await self.get_credit_balance(user_id)
            projects = await self.client.get(
                "/projects",
                params={"select": "id", "owner_id": f"eq.{user_id}"},
            )
            projects.raise_for_status()
            row["project_count"] = len(projects.json())
        return rows

    async def admin_get_user_usage(self, user_id: UUID) -> Row | None:
        profile_response = await self.client.get(
            "/profiles",
            params={
                "select": (
                    "id,email,display_name,country,auth_provider,"
                    "created_at,last_login_at"
                ),
                "id": f"eq.{user_id}",
                "limit": "1",
            },
        )
        profile_response.raise_for_status()
        profiles = profile_response.json()
        if not profiles:
            return None
        projects_response = await self.client.get(
            "/projects",
            params={
                "select": (
                    "id,title,status,source_lang,target_lang,"
                    "duration_seconds,created_at"
                ),
                "owner_id": f"eq.{user_id}",
                "order": "created_at.desc",
                "limit": "100",
            },
        )
        credits_response = await self.client.get(
            "/credit_ledger",
            params={
                "select": "id,delta_minutes,reason,project_id,created_at",
                "user_id": f"eq.{user_id}",
                "order": "created_at.desc",
                "limit": "200",
            },
        )
        projects_response.raise_for_status()
        credits_response.raise_for_status()
        return {
            "profile": profiles[0],
            "projects": projects_response.json(),
            "credits": credits_response.json(),
            "credit_balance": await self.get_credit_balance(user_id),
        }

    async def admin_list_access_logs(self, limit: int = 200) -> list[Row]:
        resp = await self.client.get(
            "/access_logs",
            params={
                "select": (
                    "id,user_id,method,path,status_code,ip_address,"
                    "user_agent,created_at,profiles(email)"
                ),
                "order": "created_at.desc",
                "limit": str(limit),
            },
        )
        resp.raise_for_status()
        rows = resp.json()
        for row in rows:
            row["email"] = (row.pop("profiles", None) or {}).get("email")
        return rows

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
        resp = await self.client.post(
            "/access_logs",
            json={
                "user_id": str(user_id) if user_id else None,
                "method": method[:16],
                "path": path[:500],
                "status_code": status_code,
                "ip_address": ip_address,
                "user_agent": (user_agent or "")[:500],
            },
        )
        resp.raise_for_status()

    # --- Stripe billing ------------------------------------------------------

    async def get_stripe_customer(self, owner_id: UUID) -> str | None:
        resp = await self.client.get(
            "/stripe_customers",
            params={
                "select": "stripe_customer_id",
                "user_id": f"eq.{owner_id}",
                "limit": "1",
            },
        )
        resp.raise_for_status()
        rows = resp.json()
        return str(rows[0]["stripe_customer_id"]) if rows else None

    async def get_user_by_stripe_customer(
        self, customer_id: str
    ) -> UUID | None:
        resp = await self.client.get(
            "/stripe_customers",
            params={
                "select": "user_id",
                "stripe_customer_id": f"eq.{customer_id}",
                "limit": "1",
            },
        )
        resp.raise_for_status()
        rows = resp.json()
        return UUID(str(rows[0]["user_id"])) if rows else None

    async def save_stripe_customer(
        self, owner_id: UUID, customer_id: str
    ) -> str:
        resp = await self.client.post(
            "/stripe_customers",
            params={"on_conflict": "user_id", "select": "stripe_customer_id"},
            headers={"Prefer": "resolution=ignore-duplicates,return=representation"},
            json={"user_id": str(owner_id), "stripe_customer_id": customer_id},
        )
        resp.raise_for_status()
        rows = resp.json()
        if rows:
            return str(rows[0]["stripe_customer_id"])
        existing = await self.get_stripe_customer(owner_id)
        assert existing is not None
        return existing

    async def process_stripe_event(self, event: Row) -> bool:
        result = await self._rpc(
            "process_stripe_event",
            {
                "p_event_id": event["event_id"],
                "p_event_type": event["event_type"],
                "p_payload": event["payload"],
                "p_user_id": str(event["user_id"]) if event.get("user_id") else None,
                "p_customer_id": event.get("customer_id"),
                "p_subscription_id": event.get("subscription_id"),
                "p_status": event.get("subscription_status"),
                "p_price_id": event.get("price_id"),
                "p_period_end": (
                    event["period_end"].isoformat()
                    if event.get("period_end")
                    else None
                ),
                "p_cancel_at_period_end": bool(event.get("cancel_at_period_end")),
                "p_credit_minutes": float(event.get("credit_minutes") or 0),
                "p_credit_reference": event.get("credit_reference"),
            },
        )
        return bool(result)

    async def process_revenuecat_event(self, event: Row) -> bool:
        result = await self._rpc(
            "process_revenuecat_event",
            {
                "p_event_id": event["event_id"],
                "p_event_type": event["event_type"],
                "p_payload": event["payload"],
                "p_user_id": str(event["user_id"]) if event.get("user_id") else None,
                "p_app_user_id": event.get("app_user_id"),
                "p_product_id": event.get("product_id"),
                "p_entitlement_ids": event.get("entitlement_ids") or [],
                "p_transaction_id": event.get("transaction_id"),
                "p_original_transaction_id": event.get("original_transaction_id"),
                "p_status": event.get("status"),
                "p_expires_at": (
                    event["expires_at"].isoformat()
                    if event.get("expires_at")
                    else None
                ),
                "p_store": event.get("store"),
                "p_credit_minutes": float(event.get("credit_minutes") or 0),
            },
        )
        return bool(result)
