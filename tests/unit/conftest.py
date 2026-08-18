from __future__ import annotations

import pytest

from app.core.usage.account_limits import AccountUsageLimitState
from app.modules.proxy.load_balancer import LoadBalancer


@pytest.fixture(autouse=True)
def _available_websocket_owner(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    marker = request.node.get_closest_marker("available_websocket_owner")
    if marker is None:
        return
    allowed_account_ids = frozenset(marker.args)
    assert allowed_account_ids and all(isinstance(account_id, str) for account_id in allowed_account_ids)

    async def check_account_usage_limit(
        _self: LoadBalancer,
        account_id: str,
    ) -> AccountUsageLimitState:
        assert account_id in allowed_account_ids, (
            f"unexpected websocket owner {account_id!r}; expected one of {sorted(allowed_account_ids)!r}"
        )
        return AccountUsageLimitState.DISABLED

    monkeypatch.setattr(LoadBalancer, "check_account_usage_limit", check_account_usage_limit)
