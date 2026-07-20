"""FastAPI dependencies wiring app-state singletons into handlers."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from .db.base import Repository
from .storage import R2Storage


def get_repository(request: Request) -> Repository:
    return request.app.state.repository


def get_storage(request: Request) -> R2Storage:
    return request.app.state.storage


Repo = Annotated[Repository, Depends(get_repository)]
Storage = Annotated[R2Storage, Depends(get_storage)]
