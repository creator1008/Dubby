from uuid import uuid4

import pytest

from app.auth import AuthenticatedUser, get_admin_user
from app.errors import UnauthorizedError


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
