from __future__ import annotations

import asyncio
import sqlite3
import threading
import weakref
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import session as session_module
from app.db.session import close_session
from app.db.sqlite_connection import FinalizingSQLiteConnection

pytestmark = pytest.mark.unit


def test_connection_closes_remaining_cursors_after_close_errors(tmp_path: Path) -> None:
    """A failing close must not strand later RETURNING statements or hide the first error."""
    path = tmp_path / "close-errors.db"
    first_error = sqlite3.OperationalError("first cursor close failure")
    second_error = sqlite3.OperationalError("second cursor close failure")
    close_calls: list[sqlite3.Cursor] = []

    class ErrorAfterCloseCursor(sqlite3.Cursor):
        def close(self) -> None:
            """Inject failures after native finalization, independent of WeakSet iteration order."""
            super().close()
            close_calls.append(self)
            if len(close_calls) == 1:
                raise first_error
            if len(close_calls) == 2:
                raise second_error

    connection = sqlite3.connect(path, factory=FinalizingSQLiteConnection)
    connection.execute("CREATE TABLE diagnostic (value INTEGER)")
    cursors = [connection.cursor(ErrorAfterCloseCursor) for _ in range(3)]
    for cursor in cursors:
        cursor.execute("INSERT INTO diagnostic VALUES (1) RETURNING value")
    try:
        with pytest.raises(sqlite3.OperationalError) as raised:
            connection.close()
        assert raised.value is first_error
        with closing(sqlite3.connect(path, timeout=0.2)) as independent, independent:
            independent.execute("INSERT INTO diagnostic VALUES (2)")
            assert independent.execute("SELECT value FROM diagnostic").fetchall() == [(2,)]
        assert len(close_calls) == len(cursors)
        assert set(close_calls) == set(cursors)
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            connection.execute("SELECT 1")
        connection.close()
        assert len(close_calls) == len(cursors)
    finally:
        connection.close()


def test_connection_finalizes_retained_cursors_and_preserves_transactions(tmp_path: Path) -> None:
    """Connection teardown rolls back unfinished writes without retaining dead cursors."""
    path = tmp_path / "transactions.db"
    connection = sqlite3.connect(path, factory=FinalizingSQLiteConnection)
    connection.execute("CREATE TABLE diagnostic (value INTEGER)")
    committed = connection.cursor()
    committed.execute("INSERT INTO diagnostic VALUES (1)")
    connection.commit()
    committed.close()
    rolled_back = connection.cursor()
    rolled_back.execute("INSERT INTO diagnostic VALUES (2)")
    connection.rollback()
    unfinished = connection.cursor()
    unfinished.execute("INSERT INTO diagnostic VALUES (3) RETURNING value")
    collected = connection.cursor()
    reference = weakref.ref(collected)
    del collected
    assert reference() is None
    connection.close()
    connection.close()
    with pytest.raises(sqlite3.ProgrammingError):
        unfinished.fetchone()
    with closing(sqlite3.connect(path, timeout=0.2)) as independent, independent:
        independent.execute("INSERT INTO diagnostic VALUES (4)")
        assert independent.execute("SELECT value FROM diagnostic").fetchall() == [(1,), (4,)]


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_role", ["main", "background"])
async def test_cancelled_returning_write_releases_lock_with_retained_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engine_role: str
) -> None:
    """Both file-backed engine roles release cancelled writes before traceback collection."""
    path = tmp_path / "cancelled.db"
    url = f"sqlite+aiosqlite:///{path}"
    if engine_role == "main":
        engine = session_module._create_main_engine(url)
    else:
        monkeypatch.setattr(session_module, "_background_engine", None)
        monkeypatch.setattr(session_module, "_background_session_factory", None)
        session_module.init_background_db(url)
        engine = session_module._background_engine
        assert engine is not None
    entered = threading.Event()
    release = threading.Event()

    def pause_write() -> int:
        """Hold SQLite execution until the test has requested cancellation."""
        entered.set()
        assert release.wait(5)
        return 1

    @event.listens_for(engine.sync_engine, "connect")
    def register_pause(connection, _record) -> None:
        """Install the cancellation barrier on each real database connection."""
        connection.create_function("pause_write", 0, pause_write)

    session = async_sessionmaker(engine)()
    retained_errors: list[BaseException] = []

    async def write() -> None:
        """Keep the cancellation traceback alive after the write task finishes."""
        try:
            await session.execute(text("INSERT INTO diagnostic VALUES (pause_write()) RETURNING value"))
        except asyncio.CancelledError as exc:
            retained_errors.append(exc)
            raise

    with closing(sqlite3.connect(path)) as independent, independent:
        independent.execute("CREATE TABLE diagnostic (value INTEGER)")
    task = asyncio.create_task(write())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        release.set()
        results = await asyncio.gather(task, return_exceptions=True)
        await close_session(session)
        assert isinstance(results[0], asyncio.CancelledError)
        assert retained_errors[0].__traceback__ is not None
        with closing(sqlite3.connect(path, timeout=0.2)) as independent, independent:
            independent.execute("INSERT INTO diagnostic VALUES (2)")
            assert independent.execute("SELECT value FROM diagnostic").fetchall() == [(2,)]
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await close_session(session)
        await engine.dispose()
