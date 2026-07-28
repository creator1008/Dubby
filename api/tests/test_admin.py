from uuid import uuid4

import pytest

from app.auth import AuthenticatedUser, get_admin_user
from app.errors import UnauthorizedError
from app.routers.admin import CreditAdjustment, UserActiveUpdate


@pytest.mark.anyio
async def test_admin_dependency_accepts_only_app_metadata_admin() -> None:
    admin = AuthenticatedUser(
        id=uuid4(),
        email="admin@example.test",
        role="authenticated",
        is_admin=True,
    )
    assert await get_admin_user(admin) is admin

    regular = AuthenticatedUser(
        id=uuid4(),
        email="user@example.test",
        role="authenticated",
    )
    with pytest.raises(UnauthorizedError):
        await get_admin_user(regular)


def test_credit_adjustment_rejects_zero() -> None:
    with pytest.raises(Exception):
        CreditAdjustment(delta_minutes=0, note="noop")


def test_user_active_update_model() -> None:
    assert UserActiveUpdate(is_active=False).is_active is False
    assert UserActiveUpdate(is_active=True).is_active is True
