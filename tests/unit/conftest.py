"""Unit-test conftest.

Installs a deterministic stub :class:`ProxyConfigProvider` so that any
account-bound outbound call site exercised by a unit test goes through
the global client instead of trying to query the database for
per-account proxy configuration. Tests that need to verify proxy-aware
behavior install their own provider via
:func:`app.core.clients.account_http.set_proxy_config_provider` and that
override survives until the next test resets it via this autouse
fixture.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.clients import account_http as account_http_module
from app.core.clients.account_http import EgressContext


class _NoProxyStubProvider:
    """Always reports "no proxy" — keeps unit tests off the DB path."""

    async def get(self, account_id: str) -> Any:  # pragma: no cover - simple stub
        return None

    async def get_egress(self, account_id: str) -> EgressContext:  # pragma: no cover - simple stub
        return EgressContext(proxy=None)


@pytest.fixture(autouse=True)
def _no_proxy_stub_provider():
    """Reset the per-account proxy provider to a deterministic stub.

    Without this, the lazy default provider would fall through to
    :class:`DatabaseProxyConfigProvider`, which opens a real session
    against ``CODEX_LB_DATABASE_URL``. Most unit tests don't run
    migrations, so that lookup raises ``OperationalError(no such
    table)``. Installing a stub here keeps the test surface fast and
    side-effect free.
    """

    account_http_module.set_proxy_config_provider(_NoProxyStubProvider())
    try:
        yield
    finally:
        account_http_module.set_proxy_config_provider(None)
