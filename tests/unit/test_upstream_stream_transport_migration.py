from __future__ import annotations

import pytest
from anyio import to_thread
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.db.migrate import run_upgrade

pytestmark = pytest.mark.unit

_PARENT = "20260830_000000_add_quota_warmup_claim_expiry"
_REVISION = "20260908_000000_replace_upstream_stream_transport_default_sentinel"


async def _insert_settings_row(connection: AsyncConnection, row_id: int, transport: str | None) -> None:
    """Insert a dashboard_settings row, filling NOT NULL columns that have no server default."""
    columns = (await connection.execute(text("PRAGMA table_info('dashboard_settings')"))).all()
    values: dict[str, object] = {"id": row_id}
    for _cid, name, col_type, notnull, dflt_value, _pk in columns:
        if name == "id" or not notnull or dflt_value is not None:
            continue
        upper = str(col_type).upper()
        if "INT" in upper or "BOOL" in upper:
            values[name] = 0
        elif "REAL" in upper or "FLOAT" in upper:
            values[name] = 0.0
        else:
            values[name] = "x"
    if transport is not None:
        values["upstream_stream_transport"] = transport
    else:
        values.pop("upstream_stream_transport", None)
    names = ", ".join(values)
    params = ", ".join(f":{name}" for name in values)
    await connection.execute(text(f"INSERT INTO dashboard_settings ({names}) VALUES ({params})"), values)


@pytest.mark.asyncio
async def test_upstream_stream_transport_default_sentinel_maps_to_auto(tmp_path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'transport.sqlite'}"

    await to_thread.run_sync(lambda: run_upgrade(db_url, _PARENT, bootstrap_legacy=False))
    engine = create_async_engine(db_url)
    try:
        async with engine.begin() as connection:
            # Earlier migrations seed row 1; force it back onto the legacy sentinel.
            updated = await connection.execute(
                text("UPDATE dashboard_settings SET upstream_stream_transport = 'default' WHERE id = 1")
            )
            if updated.rowcount == 0:
                await _insert_settings_row(connection, 1, "default")
            await _insert_settings_row(connection, 2, "websocket")

        await to_thread.run_sync(lambda: run_upgrade(db_url, _REVISION, bootstrap_legacy=False))

        async with engine.begin() as connection:
            select_rows = text("SELECT id, upstream_stream_transport FROM dashboard_settings ORDER BY id")
            rows = (await connection.execute(select_rows)).all()
            assert rows == [(1, "auto"), (2, "websocket")]
            # New rows inherit the "auto" server default instead of the sentinel.
            await _insert_settings_row(connection, 3, None)
            default_value = (
                await connection.execute(text("SELECT upstream_stream_transport FROM dashboard_settings WHERE id = 3"))
            ).scalar_one()
            assert default_value == "auto"
    finally:
        await engine.dispose()
