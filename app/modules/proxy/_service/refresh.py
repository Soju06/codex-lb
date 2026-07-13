from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from app.core.auth.refresh import RefreshError
from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.core.resilience.network_recovery import (
    PROCESS_NETWORK_UNAVAILABLE_CODE,
    ProcessNetworkRecovery,
)
from app.core.upstream_proxy import UpstreamProxyRouteError
from app.db.models import Account


class _RefreshServiceProtocol(Protocol):
    async def _ensure_fresh(
        self,
        account: Account,
        *,
        force: bool = False,
        timeout_seconds: float | None = None,
    ) -> Account: ...


async def ensure_fresh_with_budget(
    proxy: _RefreshServiceProtocol,
    account: Account,
    *,
    force: bool,
    deadline: float | None,
    remaining_budget_seconds: Callable[[float], float],
    request_id: str | None,
) -> Account:
    """Refresh one account within the caller's deadline and replay boundary."""
    recovery = ProcessNetworkRecovery(
        transport="refresh",
        request_id=request_id,
        account_id=account.id,
    )
    while True:
        remaining_seconds = None if deadline is None else remaining_budget_seconds(deadline)
        if remaining_seconds is not None and remaining_seconds <= 0:
            raise _refresh_budget_exhausted()
        try:
            refreshed = await proxy._ensure_fresh(
                account,
                force=force,
                timeout_seconds=remaining_seconds,
            )
            recovery.log_recovered()
            return refreshed
        except RefreshError as exc:
            if exc.transport_error_code == PROCESS_NETWORK_UNAVAILABLE_CODE and deadline is not None:
                decision = await recovery.wait(
                    error_code=exc.transport_error_code,
                    retryable_same_contract=exc.retryable_same_contract,
                    deadline=deadline,
                    rotate_shared_client=True,
                    failed_session=exc.failed_session,
                )
                if decision == "retry":
                    continue
                if decision == "exhausted":
                    raise _refresh_budget_exhausted() from exc
            if exc.transport_error_code == PROCESS_NETWORK_UNAVAILABLE_CODE:
                # Reading a refresh response may consume a rotating token. Keep
                # the error account-neutral without replaying that contract.
                raise ProxyResponseError(
                    502,
                    openai_error(PROCESS_NETWORK_UNAVAILABLE_CODE, exc.message),
                    failure_phase="refresh",
                    retryable_same_contract=False,
                ) from exc
            reason = _refresh_upstream_proxy_fail_closed_reason(exc)
            if reason is not None:
                raise UpstreamProxyRouteError(reason, account_id=account.id) from exc
            raise


def _refresh_budget_exhausted() -> ProxyResponseError:
    return ProxyResponseError(
        502,
        openai_error("upstream_request_timeout", "Proxy request budget exhausted"),
        failure_phase="refresh",
    )


def _refresh_upstream_proxy_fail_closed_reason(exc: RefreshError) -> str | None:
    if exc.code != "upstream_proxy_unavailable":
        return None
    reason = exc.upstream_proxy_fail_closed_reason
    if reason:
        return reason
    marker = "Upstream proxy route unavailable:"
    if exc.message.startswith(marker):
        parsed = exc.message.removeprefix(marker).strip()
        return parsed or "unavailable"
    return "unavailable"
