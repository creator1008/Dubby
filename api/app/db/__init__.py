"""Database access layer.

The API talks to storage exclusively through the :class:`~.base.Repository`
interface. Two backends exist:

- :class:`~.postgres.PostgresRepository` — asyncpg pool straight to the
  Supabase Postgres (or any Postgres). Preferred on Lightsail.
- :class:`~.supabase_rest.SupabaseRestRepository` — PostgREST over HTTPS
  using the service-role key, for environments without direct DB access.
"""

from __future__ import annotations

from ..config import Settings
from .base import Repository


def create_repository(settings: Settings) -> Repository:
    if settings.db_backend == "supabase_rest":
        from .supabase_rest import SupabaseRestRepository

        return SupabaseRestRepository(settings)

    from .postgres import PostgresRepository

    return PostgresRepository(settings)


__all__ = ["Repository", "create_repository"]
