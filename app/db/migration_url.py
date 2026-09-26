from __future__ import annotations

from sqlalchemy.engine import make_url

from app.db.sqlite_utils import normalize_sqlite_url


def to_sync_database_url(database_url: str) -> str:
    database_url = normalize_sqlite_url(database_url)
    parsed = make_url(database_url)
    driver = parsed.drivername

    if driver == "sqlite+aiosqlite":
        parsed = parsed.set(drivername="sqlite")
    elif driver == "postgresql+asyncpg":
        parsed = parsed.set(drivername="postgresql+psycopg")
    elif driver in ("mysql+asyncmy", "mysql+aiomysql"):
        parsed = parsed.set(drivername="mysql+pymysql")
    elif driver in ("mariadb+asyncmy", "mariadb+aiomysql"):
        # A ``mariadb+…`` URL keeps SQLAlchemy's MariaDB compilation (its
        # ``ON DUPLICATE KEY UPDATE`` uses ``VALUES(col)`` and not MySQL 8's row
        # alias, which MariaDB rejects); the synchronous side must stay MariaDB.
        parsed = parsed.set(drivername="mariadb+pymysql")

    return parsed.render_as_string(hide_password=False)
