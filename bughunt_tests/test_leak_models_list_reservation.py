"""CLASS 1 (lease/reservation leaks): models-catalog routes leak the API-key reservation.

``_build_models_response`` (app/modules/proxy/api.py:3434) and
``_build_codex_models_response`` (app/modules/proxy/api.py:3325) each take an
API-key usage reservation, then ``await _list_enabled_source_catalog_models(...)``
(a real DB round trip), and only afterwards reach their single
``await _release_reservation(reservation)`` statement.  Neither function wraps
the span in ``try/finally``, so every non-return exit between the acquire and
that one release -- a DB error from the catalog read, or plain task
cancellation on client disconnect -- leaves the ``api_key_usage_reservations``
row in ``status='reserved'`` with its reserved deltas still charged against the
key's limits until the 6h/24h janitor reclaims it.

Sibling routes on the same file settle correctly with ``try/finally``
(``backend_files_create`` api.py:2194-2231, ``backend_files_finalize``
api.py:2238-2275, ``_source_audio_transcription_response`` api.py:4098-4141),
which is what makes these two the uncovered siblings.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest
from sqlalchemy import select

from app.db.models import ApiKeyUsageReservation
from app.db.session import SessionLocal
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyCreateData, ApiKeysService, LimitRuleInput
from app.modules.proxy import api as proxy_api
from tests.conftest import _reset_db_state  # noqa: F401  (underscore fixture is skipped by `import *`)


@dataclass(frozen=True, slots=True)
class _FakeReservation:
    reservation_id: str = "resv-bughunt-models"
    key_id: str = "key-bughunt"
    model: str = ""
    has_applicable_limits: bool = True


def _install(monkeypatch, released: list[str], catalog):
    async def fake_enforce(api_key, **kwargs):
        del api_key, kwargs
        return _FakeReservation()

    async def fake_release(reservation):
        if reservation is not None:
            released.append(reservation.reservation_id)

    monkeypatch.setattr(proxy_api, "_enforce_request_limits", fake_enforce)
    monkeypatch.setattr(proxy_api, "_release_reservation", fake_release)
    monkeypatch.setattr(proxy_api, "_list_enabled_source_catalog_models", catalog)


async def test_v1_models_reservation_released_when_source_catalog_read_fails(monkeypatch):
    released: list[str] = []

    async def boom(api_key, **kwargs):
        del api_key, kwargs
        raise RuntimeError("model_sources catalog read failed")

    _install(monkeypatch, released, boom)

    with pytest.raises(RuntimeError):
        await proxy_api._build_models_response(None)

    assert released == ["resv-bughunt-models"], (
        "GET /v1/models leaked the API-key usage reservation: the catalog read raised between "
        "_enforce_request_limits (api.py:3435) and the single _release_reservation (api.py:3470)"
    )


async def test_codex_models_reservation_released_when_source_catalog_read_fails(monkeypatch):
    released: list[str] = []

    async def boom(api_key, **kwargs):
        del api_key, kwargs
        raise RuntimeError("model_sources catalog read failed")

    _install(monkeypatch, released, boom)

    with pytest.raises(RuntimeError):
        await proxy_api._build_codex_models_response(None)

    assert released == ["resv-bughunt-models"], (
        "GET /backend-api/codex/models leaked the API-key usage reservation: the catalog read raised "
        "between _enforce_request_limits (api.py:3326) and its releases (api.py:3375 / api.py:3430)"
    )


async def test_v1_models_reservation_released_on_client_disconnect_cancellation(monkeypatch):
    released: list[str] = []
    entered = asyncio.Event()

    async def hang(api_key, **kwargs):
        del api_key, kwargs
        entered.set()
        await asyncio.sleep(3600)
        return []

    _install(monkeypatch, released, hang)

    task = asyncio.create_task(proxy_api._build_models_response(None))
    await asyncio.wait_for(entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert released == ["resv-bughunt-models"], (
        "GET /v1/models leaked the API-key usage reservation when the request task was cancelled "
        "(client disconnect) while awaiting _list_enabled_source_catalog_models (api.py:3447)"
    )


async def test_v1_models_reservation_row_stays_reserved_in_db_when_catalog_read_fails(db_setup, monkeypatch):
    """End-to-end proof against the real reservation table (no fakes on the acquire side)."""

    async def boom(api_key, **kwargs):
        del api_key, kwargs
        raise RuntimeError("model_sources catalog read failed")

    monkeypatch.setattr(proxy_api, "_list_enabled_source_catalog_models", boom)

    async with SessionLocal() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        created = await service.create_key(
            ApiKeyCreateData(
                name="bughunt-models-list-leak",
                allowed_models=None,
                expires_at=None,
                limits=[
                    LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=50_000),
                ],
            )
        )

    with pytest.raises(RuntimeError):
        await proxy_api._build_models_response(created)

    async with SessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(ApiKeyUsageReservation).where(ApiKeyUsageReservation.api_key_id == created.id)
                )
            )
            .scalars()
            .all()
        )
        statuses = [row.status for row in rows]
        limits = await ApiKeysRepository(session).get_limits_by_key(created.id)
        held = limits[0].current_value

    assert statuses == ["released"], (
        f"GET /v1/models left the reservation row in {statuses!r}; "
        f"{held} tokens of the key's weekly total_tokens limit stay charged until the janitor reclaims them"
    )
    assert held == 0


async def test_codex_models_reservation_row_released_in_db_when_catalog_read_fails(db_setup, monkeypatch):
    """The Codex-native catalog route must release the real reservation too."""

    async def boom(api_key, **kwargs):
        del api_key, kwargs
        raise RuntimeError("model_sources catalog read failed")

    monkeypatch.setattr(proxy_api, "_list_enabled_source_catalog_models", boom)

    async with SessionLocal() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        created = await service.create_key(
            ApiKeyCreateData(
                name="bughunt-codex-models-list-leak",
                allowed_models=None,
                expires_at=None,
                limits=[
                    LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=50_000),
                ],
            )
        )

    with pytest.raises(RuntimeError):
        await proxy_api._build_codex_models_response(created)

    async with SessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(ApiKeyUsageReservation).where(ApiKeyUsageReservation.api_key_id == created.id)
                )
            )
            .scalars()
            .all()
        )
        statuses = [row.status for row in rows]
        limits = await ApiKeysRepository(session).get_limits_by_key(created.id)
        held = limits[0].current_value

    assert statuses == ["released"], (
        f"GET /backend-api/codex/models left the reservation row in {statuses!r}; "
        f"{held} tokens of the key's weekly total_tokens limit stay charged until the janitor reclaims them"
    )
    assert held == 0


async def test_control_v1_models_releases_reservation_on_success(monkeypatch):
    """Positive control: the same harness observes the release on the normal return path."""
    released: list[str] = []

    async def empty_catalog(api_key, **kwargs):
        del api_key, kwargs
        return []

    _install(monkeypatch, released, empty_catalog)

    await proxy_api._build_models_response(None)

    assert released == ["resv-bughunt-models"]


async def test_control_codex_models_releases_reservation_on_success(monkeypatch):
    released: list[str] = []

    async def empty_catalog(api_key, **kwargs):
        del api_key, kwargs
        return []

    _install(monkeypatch, released, empty_catalog)

    await proxy_api._build_codex_models_response(None)

    assert released == ["resv-bughunt-models"]
