from __future__ import annotations

from sqlalchemy.engine import make_url

from app.db.sqlite_utils import normalize_sqlite_url


def to_sync_database_url(database_url: str) -> str:
    database_url = normalize_sqlite_url(database_url)
    if database_url.startswith("sqlite+aiosqlite:"):
        # SQLAlchemy >= 2.1 percent-encodes sqlite database paths when re-rendered
        # through make_url, which breaks literal '%' characters. Swap only the
        # driver prefix and keep the path verbatim.
        return "sqlite:" + database_url[len("sqlite+aiosqlite:") :]
    if database_url.startswith("sqlite:"):
        return database_url
    parsed = make_url(database_url)
    driver = parsed.drivername

    if driver == "sqlite+aiosqlite":
        parsed = parsed.set(drivername="sqlite")
    elif driver == "postgresql+asyncpg":
        parsed = parsed.set(drivername="postgresql+psycopg")

    return parsed.render_as_string(hide_password=False)
