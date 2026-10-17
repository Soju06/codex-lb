from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import cast

import jwt
import prometheus_client
import pytest
from httpx import ASGITransport, AsyncClient
from prometheus_client.parser import text_string_to_metric_families
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.metrics import prometheus as metrics
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.proxy.account_cache import RoutingAvailabilityCache
from tests.simulation.virtual_time import VirtualClock

pytestmark = pytest.mark.integration


def _scrape_registry() -> prometheus_client.CollectorRegistry:
    """Require a real Prometheus collector at the integration-test boundary."""
    registry = metrics.make_scrape_registry()
    assert isinstance(registry, prometheus_client.CollectorRegistry)
    return registry


def _account(account_id: str, status: AccountStatus, token: str = "unknown-expiry") -> Account:
    """Build encrypted account credentials with controlled access-token contents."""
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=f"{account_id}@example.test",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt(token),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=datetime.now(timezone.utc).replace(tzinfo=None),
        status=status,
    )


def _samples(body: str) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
    """Read only account-pool series from real Prometheus exposition."""
    return {
        (sample.name, tuple(sorted(sample.labels.items()))): sample.value
        for family in text_string_to_metric_families(body)
        for sample in family.samples
        if sample.name in {"codex_lb_accounts_total", "codex_lb_accounts_available"}
    }


def _expected(counts: dict[AccountStatus, int], available: int) -> dict:
    """Include every status so missing zeroes and stale values fail assertions."""
    return {
        **{("codex_lb_accounts_total", (("status", status.value),)): counts.get(status, 0) for status in AccountStatus},
        ("codex_lb_accounts_available", ()): available,
    }


@pytest.mark.asyncio
async def test_lifespan_wires_fresh_account_metrics_into_standalone_scrape_app(app_instance, monkeypatch) -> None:
    """Ensure production startup wires refreshes into the standalone metrics app."""
    import uvicorn
    from starlette.types import ASGIApp

    monkeypatch.setattr(get_settings(), "metrics_enabled", True)
    apps: list[ASGIApp] = []

    class CapturedMetricsServer:
        def __init__(self, config: uvicorn.Config) -> None:
            """Capture the configured ASGI app without binding a host port."""
            apps.append(cast(ASGIApp, config.app))
            self.should_exit = False

        async def serve(self) -> None:
            """Complete the server task without opening a listener."""
            return None

    monkeypatch.setattr("app.core.server.SignalNeutralServer", CapturedMetricsServer)
    async with app_instance.router.lifespan_context(app_instance):
        assert len(apps) == 1
        async with AsyncClient(transport=ASGITransport(app=apps[0]), base_url="http://metrics") as client:
            response = await client.get("/metrics")
            assert response.status_code == 200
            assert _samples(response.text) == _expected({}, 0)
            async with SessionLocal() as session:
                session.add(_account("after-startup", AccountStatus.ACTIVE))
                await session.commit()
            response = await client.get("/metrics")
            assert response.status_code == 200
            assert _samples(response.text) == _expected({AccountStatus.ACTIVE: 1}, 1)


@pytest.mark.asyncio
async def test_account_cache_refresh_populates_actual_metrics_scrape(db_setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """Check every status and pending deletion through real metrics exposition."""
    monkeypatch.setattr(get_settings(), "metrics_enabled", True)
    cache = RoutingAvailabilityCache(SessionLocal)
    app = prometheus_client.make_asgi_app(registry=_scrape_registry())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://metrics") as client:
        await cache.refresh_from_db()
        response = await client.get("/metrics")
        assert response.status_code == 200
        assert _samples(response.text) == _expected({}, 0)

        async with SessionLocal() as session:
            session.add_all(_account(status.value, status) for status in AccountStatus)
            pending = _account("pending", AccountStatus.ACTIVE)
            pending.delete_requested_at = datetime.now(timezone.utc).replace(tzinfo=None)
            session.add(pending)
            await session.commit()
        await cache.refresh_from_db()
        response = await client.get("/metrics")
        assert _samples(response.text) == _expected(dict.fromkeys(AccountStatus, 1), 2)
        # The metrics projection must not change the routing cache's status semantics.
        assert cache.is_unavailable("active") is False
        assert cache.is_unavailable("rate_limited") is False
        assert cache.is_unavailable("paused") is True


@pytest.mark.asyncio
async def test_scrape_refreshes_status_deletion_and_token_expiry_without_proxy_traffic(
    db_setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep scrapes current across expiry and deletion without proxy traffic."""
    from app.core.metrics.middleware import MetricsRefreshMiddleware

    monkeypatch.setattr(get_settings(), "metrics_enabled", True)
    clock = VirtualClock(epoch_value=2_000_000_000.0)
    cache = RoutingAvailabilityCache(SessionLocal, clock=clock)
    app = MetricsRefreshMiddleware(
        prometheus_client.make_asgi_app(registry=_scrape_registry()),
        refresh=cache.refresh_from_db,
    )
    future_token = jwt.encode({"exp": clock.time() + 60}, "test-key-with-at-least-32-characters", algorithm="HS256")
    expired_token = jwt.encode({"exp": clock.time() - 60}, "test-key-with-at-least-32-characters", algorithm="HS256")
    async with SessionLocal() as session:
        session.add_all(
            [
                _account("active", AccountStatus.ACTIVE, expired_token),
                _account("future", AccountStatus.REAUTH_REQUIRED, future_token),
                _account("expired", AccountStatus.REAUTH_REQUIRED, expired_token),
                _account("unknown", AccountStatus.REAUTH_REQUIRED),
            ]
        )
        unreadable = _account("unreadable", AccountStatus.REAUTH_REQUIRED)
        unreadable.access_token_encrypted = b"unreadable"
        session.add(unreadable)
        await session.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://metrics") as client:
        response = await client.get("/metrics")
        assert response.status_code == 200
        assert _samples(response.text) == _expected({AccountStatus.ACTIVE: 1, AccountStatus.REAUTH_REQUIRED: 4}, 4)

        clock.advance(60)
        response = await client.get("/metrics")
        assert _samples(response.text) == _expected({AccountStatus.ACTIVE: 1, AccountStatus.REAUTH_REQUIRED: 4}, 3)

        async with SessionLocal() as session:
            await session.execute(update(Account).where(Account.id == "active").values(status=AccountStatus.PAUSED))
            await session.execute(delete(Account).where(Account.id == "unknown"))
            await session.execute(
                update(Account)
                .where(Account.id == "unreadable")
                .values(delete_requested_at=datetime.now(timezone.utc).replace(tzinfo=None))
            )
            await session.commit()
        response = await client.get("/metrics")
        assert _samples(response.text) == _expected({AccountStatus.PAUSED: 1, AccountStatus.REAUTH_REQUIRED: 2}, 0)

        async with SessionLocal() as session:
            await session.execute(delete(Account))
            await session.commit()
        response = await client.get("/metrics")
        assert _samples(response.text) == _expected({}, 0)


@pytest.mark.asyncio
async def test_failed_metrics_refresh_does_not_expose_stale_account_counts(db_setup, monkeypatch) -> None:
    """Fail a scrape without returning stale counts or internal exception details."""
    from app.core.metrics.middleware import MetricsRefreshMiddleware

    monkeypatch.setattr(get_settings(), "metrics_enabled", True)
    cache = RoutingAvailabilityCache(SessionLocal)
    async with SessionLocal() as session:
        session.add(_account("active", AccountStatus.ACTIVE))
        await session.commit()
    await cache.refresh_from_db()

    async def fail_refresh() -> None:
        """Model a failed database read after a populated snapshot."""
        raise RuntimeError("database unavailable")

    app = MetricsRefreshMiddleware(prometheus_client.make_asgi_app(registry=_scrape_registry()), refresh=fail_refresh)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://metrics") as client:
        response = await client.get("/metrics")
    assert response.status_code == 503
    assert "codex_lb_accounts" not in response.text
    assert "database unavailable" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("prometheus_available", [True, False])
async def test_cache_refresh_skips_metric_token_decryption_when_disabled(
    db_setup, monkeypatch, prometheus_available: bool
) -> None:
    """Preserve routing-cache reads without decryption when metrics are disabled."""
    monkeypatch.setattr(metrics, "PROMETHEUS_AVAILABLE", prometheus_available)
    monkeypatch.setattr(get_settings(), "metrics_enabled", not prometheus_available)
    async with SessionLocal() as session:
        session.add(_account("reauth", AccountStatus.REAUTH_REQUIRED))
        await session.commit()

    def unexpected_encryptor():
        """Reject decryption attempted by disabled metric publication."""
        raise AssertionError("disabled metrics must not decrypt tokens")

    monkeypatch.setattr("app.modules.proxy.account_cache.TokenEncryptor", unexpected_encryptor)
    cache = RoutingAvailabilityCache(SessionLocal)
    await cache.refresh_from_db()
    assert cache.seeded
    assert cache.is_unavailable("reauth") is False


@pytest.mark.asyncio
async def test_overlapping_scrape_and_invalidation_preserve_newer_snapshot_and_local_mark(
    db_setup, monkeypatch
) -> None:
    """Prevent an old read from overwriting a committed pause or its local mark."""
    monkeypatch.setattr(get_settings(), "metrics_enabled", True)
    read_started = asyncio.Event()
    release_read = asyncio.Event()
    async with SessionLocal() as session:
        session.add(_account("account", AccountStatus.ACTIVE))
        await session.commit()

    class DelayedSession:
        def __init__(self, session: AsyncSession) -> None:
            """Wrap one real database session for a controllable refresh race."""
            self.inner = session

        async def execute(self, statement):
            """Hold the first read result until a newer status is committed."""
            result = await self.inner.execute(statement)
            read_started.set()
            await release_read.wait()
            return result

        def __getattr__(self, name: str):
            """Preserve the real session's cleanup and transaction interface."""
            return getattr(self.inner, name)

    calls = 0

    def session_factory() -> AsyncSession:
        """Delay only the initial read so the next refresh can observe the pause."""
        nonlocal calls
        calls += 1
        session = SessionLocal()
        return cast(AsyncSession, DelayedSession(session)) if calls == 1 else session

    cache = RoutingAvailabilityCache(session_factory)
    first = asyncio.create_task(cache.refresh_from_db())
    second = None
    try:
        await asyncio.wait_for(read_started.wait(), timeout=5)
        cache.mark_unavailable("account")
        async with SessionLocal() as session:
            await session.execute(update(Account).values(status=AccountStatus.PAUSED))
            await session.commit()
        second = asyncio.create_task(cache.refresh_from_db())
        # One scheduling turn reaches the refresh lock before either DB read
        # can progress. An overlapping refresh must not open a second session.
        await asyncio.sleep(0)
        assert calls == 1
        release_read.set()
        await first
        await second
    finally:
        release_read.set()
        await asyncio.gather(first, *([second] if second is not None else []), return_exceptions=True)
    assert calls == 2
    assert cache.is_unavailable("account") is True
    app = prometheus_client.make_asgi_app(registry=_scrape_registry())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://metrics") as client:
        response = await client.get("/metrics")
    assert _samples(response.text) == _expected({AccountStatus.PAUSED: 1}, 0)
