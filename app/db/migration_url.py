from __future__ import annotations

from sqlalchemy.engine import make_url


def to_sync_database_url(database_url: str) -> str:
    parsed = make_url(database_url)
    driver = parsed.drivername

    if driver == "sqlite+aiosqlite":
        parsed = parsed.set(drivername="sqlite")
    elif driver == "postgresql+asyncpg":
        parsed = parsed.set(drivername="postgresql+psycopg")

    return parsed.render_as_string(hide_password=False)
