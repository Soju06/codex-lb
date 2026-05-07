from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError

pytestmark = pytest.mark.unit


def _bridge_ring_ok():
    from app.modules.health.schemas import BridgeRingInfo

    return BridgeRingInfo(
        ring_fingerprint="abc",
        ring_size=0,
        instance_id="pod-a",
        is_member=False,
    )


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

    with (
        patch("app.core.draining._draining", False),
        patch("app.core.startup._bridge_durable_schema_ready", True),
        patch("app.core.startup._bridge_registration_complete", True),
        patch("app.modules.health.api.get_session") as mock_get_session,
        patch("app.modules.health.api._get_bridge_ring_info", new=AsyncMock(return_value=_bridge_ring_ok())),
    ):

        async def mock_get_session_context():
            yield mock_session

        mock_get_session.return_value = mock_get_session_context()

        response = await health_ready()
        assert response.status == "ok"
        assert response.checks == {"database": "ok"}


@pytest.mark.asyncio
async def test_health_ready_db_error():
    from app.modules.health.api import health_ready

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=OperationalError("Connection failed", None, Exception("DB error")))

    with (
        patch("app.core.startup._bridge_durable_schema_ready", True),
        patch("app.core.startup._bridge_registration_complete", True),
        patch("app.modules.health.api.get_session") as mock_get_session,
    ):

        async def mock_get_session_context():
            yield mock_session

        mock_get_session.return_value = mock_get_session_context()

        with pytest.raises(HTTPException) as exc_info:
            await health_ready()
        assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_health_ready_draining():
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

    with (
        patch("app.core.draining._draining", False),
        patch("app.core.startup._bridge_durable_schema_ready", True),
        patch("app.core.startup._bridge_registration_complete", True),
        patch("app.modules.health.api.get_session") as mock_get_session,
        patch("app.modules.health.api._get_bridge_ring_info", new=AsyncMock(return_value=_bridge_ring_ok())),
    ):

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

    with (
        patch("app.core.draining._draining", False),
        patch("app.core.startup._bridge_durable_schema_ready", True),
        patch("app.core.startup._bridge_registration_complete", True),
        patch("app.modules.health.api.get_session") as mock_get_session,
        patch("app.modules.health.api._get_bridge_ring_info", new=AsyncMock(return_value=_bridge_ring_ok())),
    ):
        with patch("app.modules.health.api.get_settings", return_value=SimpleNamespace(circuit_breaker_enabled=False)):

            async def mock_get_session_context():
                yield mock_session

            mock_get_session.return_value = mock_get_session_context()

            response = await health_ready()

    assert response.status == "ok"
    assert response.checks == {"database": "ok"}


@pytest.mark.asyncio
async def test_health_ready_fails_when_active_ring_exists_but_instance_is_missing():
    from app.modules.health.api import health_ready
    from app.modules.health.schemas import BridgeRingInfo

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()

    with (
        patch("app.core.draining._draining", False),
        patch("app.core.startup._bridge_durable_schema_ready", True),
        patch("app.core.startup._bridge_registration_complete", True),
        patch("app.modules.health.api.get_session") as mock_get_session,
        patch("app.modules.health.api.get_settings") as mock_get_settings,
        patch("app.modules.health.api._get_bridge_ring_info", new=AsyncMock()) as mock_bridge_ring,
    ):
        mock_get_settings.return_value = MagicMock(http_responses_session_bridge_enabled=True)
        mock_bridge_ring.return_value = BridgeRingInfo(
            ring_fingerprint="abc",
            ring_size=2,
            instance_id="pod-a",
            is_member=False,
        )

        async def mock_get_session_context():
            yield mock_session

        mock_get_session.return_value = mock_get_session_context()

        with pytest.raises(HTTPException) as exc_info:
            await health_ready()

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Service is not an active bridge ring member"


@pytest.mark.asyncio
async def test_health_ready_allows_empty_bridge_ring_while_instance_registers():
    from app.modules.health.api import health_ready
    from app.modules.health.schemas import BridgeRingInfo

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()

    with (
        patch("app.core.draining._draining", False),
        patch("app.core.startup._bridge_durable_schema_ready", True),
        patch("app.core.startup._bridge_registration_complete", True),
        patch("app.modules.health.api.get_session") as mock_get_session,
        patch("app.modules.health.api.get_settings") as mock_get_settings,
        patch("app.modules.health.api._get_bridge_ring_info", new=AsyncMock()) as mock_bridge_ring,
    ):
        mock_get_settings.return_value = MagicMock(http_responses_session_bridge_enabled=True)
        mock_bridge_ring.return_value = BridgeRingInfo(
            ring_fingerprint="abc",
            ring_size=0,
            instance_id="pod-a",
            is_member=False,
        )

        async def mock_get_session_context():
            yield mock_session

        mock_get_session.return_value = mock_get_session_context()

        response = await health_ready()

    assert response.status == "ok"


@pytest.mark.asyncio
async def test_health_ready_fails_when_bridge_ring_lookup_errors():
    from app.modules.health.api import health_ready
    from app.modules.health.schemas import BridgeRingInfo

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()

    with (
        patch("app.core.draining._draining", False),
        patch("app.core.startup._bridge_durable_schema_ready", True),
        patch("app.core.startup._bridge_registration_complete", True),
        patch("app.modules.health.api.get_session") as mock_get_session,
        patch("app.modules.health.api.get_settings") as mock_get_settings,
        patch("app.modules.health.api._get_bridge_ring_info", new=AsyncMock()) as mock_bridge_ring,
    ):
        mock_get_settings.return_value = MagicMock(http_responses_session_bridge_enabled=True)
        mock_bridge_ring.return_value = BridgeRingInfo(
            ring_fingerprint=None,
            ring_size=0,
            instance_id="pod-a",
            is_member=False,
            error="unavailable: ProgrammingError",
        )

        async def mock_get_session_context():
            yield mock_session

        mock_get_session.return_value = mock_get_session_context()

        with pytest.raises(HTTPException) as exc_info:
            await health_ready()

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Service bridge ring metadata is unavailable"


@pytest.mark.asyncio
async def test_health_ready_fails_when_bridge_registration_is_not_complete():
    from app.modules.health.api import health_ready

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()

    with (
        patch("app.core.draining._draining", False),
        patch("app.core.startup._bridge_durable_schema_ready", True),
        patch("app.core.startup._bridge_registration_complete", False),
        patch("app.modules.health.api.get_session") as mock_get_session,
        patch(
            "app.modules.health.api.get_settings", return_value=MagicMock(http_responses_session_bridge_enabled=True)
        ),
        patch("app.modules.health.api._get_bridge_ring_info", new=AsyncMock(return_value=_bridge_ring_ok())),
    ):

        async def mock_get_session_context():
            yield mock_session

        mock_get_session.return_value = mock_get_session_context()

        with pytest.raises(HTTPException) as exc_info:
            await health_ready()

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Service bridge registration is not complete"


@pytest.mark.asyncio
async def test_health_ready_fails_when_bridge_durable_schema_is_not_ready():
    from app.modules.health import api as health_api

    with (
        patch("app.core.draining._draining", False),
        patch("app.core.startup._bridge_durable_schema_ready", False),
        patch("app.core.startup._bridge_registration_complete", True),
        patch("app.modules.health.api.get_settings") as mock_settings,
        patch("app.modules.health.api.get_session") as mock_get_session,
    ):
        mock_settings.return_value.http_responses_session_bridge_enabled = True
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock())

        async def session_generator():
            yield mock_session

        mock_get_session.return_value = session_generator()

        with pytest.raises(HTTPException) as exc_info:
            await health_api.health_ready()

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Service bridge durable schema is not ready"


@pytest.mark.asyncio
async def test_internal_drain_start_sets_draining_and_marks_bridge_sessions():
    from app.modules.health.api import start_internal_drain

    proxy_service = SimpleNamespace(mark_http_bridge_draining=AsyncMock())
    request = SimpleNamespace(
        client=SimpleNamespace(host="127.0.0.1"),
        app=SimpleNamespace(state=SimpleNamespace(proxy_service=proxy_service)),
    )

    with (
        patch("app.core.shutdown.set_bridge_drain_active") as set_bridge_drain_active,
        patch("app.core.shutdown.set_draining") as set_draining,
    ):
        response = await start_internal_drain(cast(Any, request))

    set_bridge_drain_active.assert_called_once_with(True)
    set_draining.assert_called_once_with(True)
    proxy_service.mark_http_bridge_draining.assert_awaited_once()
    assert response.status == "ok"
    assert response.checks == {"draining": "ok"}


@pytest.mark.asyncio
async def test_internal_drain_start_rejects_non_loopback_clients():
    from app.modules.health.api import start_internal_drain

    request = SimpleNamespace(
        client=SimpleNamespace(host="8.8.8.8"),
        app=SimpleNamespace(state=SimpleNamespace(proxy_service=None)),
    )

    with pytest.raises(HTTPException) as exc_info:
        await start_internal_drain(cast(Any, request))

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_internal_drain_status_reports_shutdown_state():
    from app.modules.health.api import internal_drain_status

    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))

    with (
        patch("app.core.shutdown.is_draining", return_value=True),
        patch("app.core.shutdown.is_bridge_drain_active", return_value=True),
        patch("app.core.shutdown.get_in_flight", return_value=2),
    ):
        response = await internal_drain_status(cast(Any, request))

    assert response.status == "ok"
    assert response.checks == {
        "draining": "true",
        "bridge_drain_active": "true",
        "in_flight": "2",
    }


@pytest.mark.asyncio
async def test_internal_drain_status_rejects_non_loopback_clients():
    from app.modules.health.api import internal_drain_status

    request = SimpleNamespace(client=SimpleNamespace(host="8.8.8.8"))

    with pytest.raises(HTTPException) as exc_info:
        await internal_drain_status(cast(Any, request))

    assert exc_info.value.status_code == 403
