"""Administrator-only user, usage, access-log, and credit operations."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from ..auth import AdminUser
from ..deps import Repo

router = APIRouter(prefix="/v1/admin", tags=["admin"])


class CreditAdjustment(BaseModel):
    delta_minutes: float = Field(ge=-100000, le=100000)
    note: str = Field(min_length=2, max_length=300)

    @field_validator("delta_minutes")
    @classmethod
    def nonzero_delta(cls, value: float) -> float:
        if value == 0:
            raise ValueError("delta_minutes must not be zero")
        return value


@router.get("/users")
async def list_users(
    _admin: AdminUser,
    repo: Repo,
    query: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    return await repo.admin_list_users(query=query, limit=limit)


@router.get("/users/{user_id}")
async def get_user_usage(
    user_id: UUID,
    _admin: AdminUser,
    repo: Repo,
) -> dict:
    result = await repo.admin_get_user_usage(user_id)
    if result is None:
        raise HTTPException(404, "User not found")
    return result


@router.post("/users/{user_id}/credits")
async def adjust_user_credits(
    user_id: UUID,
    body: CreditAdjustment,
    admin: AdminUser,
    repo: Repo,
) -> dict:
    if await repo.admin_get_user_usage(user_id) is None:
        raise HTTPException(404, "User not found")
    entry = await repo.add_credit_entry(
        user_id,
        delta_minutes=body.delta_minutes,
        reason="admin_adjust",
        admin_note=body.note,
        adjusted_by=admin.id,
    )
    return {
        "entry": entry,
        "balance_minutes": await repo.get_credit_balance(user_id),
        "adjusted_by": str(admin.id),
        "note": body.note,
    }


@router.get("/access-logs")
async def list_access_logs(
    _admin: AdminUser,
    repo: Repo,
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict]:
    return await repo.admin_list_access_logs(limit=limit)
