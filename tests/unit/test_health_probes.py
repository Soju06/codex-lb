from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_health_live_always_ok():
    from app.modules.health.api import health_live

    response = await health_live()
    assert response.status == "ok"


@pytest.mark.asyncio
async def test_health_startup_when_complete():
    from app.modules.health.api import health_startup

    with patch("app.core.startup._startup_complete", True):
        response = await health_startup()
        assert response.status == "ok"


@pytest.mark.asyncio
async def test_health_startup_when_not_complete():
    from fastapi import HTTPException

    from app.modules.health.api import health_startup

    with patch("app.core.startup._startup_complete", False):
        with pytest.raises(HTTPException) as exc_info:
            await health_startup()
        assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_health_ready_db_ok():
    from app.modules.health.api import health_ready

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()

    with patch("app.modules.health.api.get_session") as mock_get_session:

        async def mock_get_session_context():
            yield mock_session

        mock_get_session.return_value = mock_get_session_context()

        response = await health_ready()
        assert response.status == "ok"
        assert response.checks == {"database": "ok"}


@pytest.mark.asyncio
async def test_health_ready_db_error():
    from fastapi import HTTPException

    from app.modules.health.api import health_ready

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=OperationalError("Connection failed", None, Exception("DB error")))

    with patch("app.modules.health.api.get_session") as mock_get_session:

        async def mock_get_session_context():
            yield mock_session

        mock_get_session.return_value = mock_get_session_context()

        with pytest.raises(HTTPException) as exc_info:
            await health_ready()
        assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_health_ready_draining():
    from fastapi import HTTPException

    from app.modules.health.api import health_ready

    with patch("builtins.__import__") as mock_import:
        mock_draining = MagicMock()
        mock_draining._draining = True

        def import_side_effect(name, *args, **kwargs):
            if name == "app.core.draining":
                return mock_draining
            return __import__(name, *args, **kwargs)

        mock_import.side_effect = import_side_effect

        with pytest.raises(HTTPException) as exc_info:
            await health_ready()
        assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_health_ready_ignores_upstream_state():
    from app.core.resilience.degradation import set_degraded
    from app.modules.health.api import health_ready

    set_degraded("upstream circuit breaker is open")

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()

    with patch("app.core.draining._draining", False), patch("app.modules.health.api.get_session") as mock_get_session:

        async def mock_get_session_context():
            yield mock_session

        mock_get_session.return_value = mock_get_session_context()

        response = await health_ready()

    assert response.status == "ok"
    assert response.checks == {"database": "ok"}


@pytest.mark.asyncio
async def test_health_ready_circuit_breaker_disabled_returns_200():
    from types import SimpleNamespace

    from app.modules.health.api import health_ready

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()

    with patch("app.core.draining._draining", False), patch("app.modules.health.api.get_session") as mock_get_session:
        with patch("app.modules.health.api.get_settings", return_value=SimpleNamespace(circuit_breaker_enabled=False)):

            async def mock_get_session_context():
                yield mock_session

            mock_get_session.return_value = mock_get_session_context()

            response = await health_ready()

    assert response.status == "ok"
    assert response.checks == {"database": "ok"}
