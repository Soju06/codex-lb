from __future__ import annotations

import asyncio
import errno
import logging
import socket
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

import aiohttp
from aiohttp_socks import ProxyConnectionError as SocksProxyConnectionError
from python_socks import ProxyConnectionError as PythonSocksProxyConnectionError

from app.core.clients.http import refresh_http_client_after_network_failure
from app.core.utils.retry import backoff_seconds

logger = logging.getLogger(__name__)

PROCESS_NETWORK_UNAVAILABLE_CODE = "proxy_network_unavailable"

_TRANSIENT_DNS_ERROR_NUMBERS = frozenset(
    value for name in ("EAI_AGAIN", "EAI_FAIL") if isinstance((value := getattr(socket, name, None)), int)
)
_PERMANENT_DNS_ERROR_NUMBERS = frozenset(
    value for name in ("EAI_NONAME",) if isinstance((value := getattr(socket, name, None)), int)
)
_ROUTE_ERROR_NUMBERS = frozenset(
    value
    for name in ("ENETDOWN", "ENETUNREACH", "EHOSTDOWN", "EHOSTUNREACH", "ENONET")
    if isinstance((value := getattr(errno, name, None)), int)
)
_MAX_RETRY_DELAY_SECONDS = 5.0
# Retiring a known-bad generation protects later callers rather than retrying
# the failed request, so it cannot inherit an already-expired request deadline.
# Keep that lifecycle cleanup independently bounded in case client construction
# or lock acquisition stalls.
_CONCRETE_FAILED_GENERATION_ROTATION_TIMEOUT_SECONDS = 5.0

NetworkRecoveryDecision = Literal["not_applicable", "retry", "exhausted"]


def _exception_chain(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    pending: list[BaseException] = [exc]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        if current.__context__ is not None:
            pending.append(current.__context__)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if isinstance(current, aiohttp.ClientConnectorError):
            pending.append(current.os_error)


def is_process_network_failure(exc: BaseException, *, include_permanent_dns: bool = True) -> bool:
    """Return whether an exception represents host-wide DNS or route loss."""

    for current in _exception_chain(exc):
        if isinstance(current, socket.gaierror):
            if current.errno in _TRANSIENT_DNS_ERROR_NUMBERS:
                return True
            if include_permanent_dns and current.errno in _PERMANENT_DNS_ERROR_NUMBERS:
                return True
        if isinstance(current, OSError) and current.errno in _ROUTE_ERROR_NUMBERS:
            return True
    return False


def is_pre_dispatch_connection_failure(exc: BaseException) -> bool:
    """Return whether a typed connector failure proves dispatch never began."""

    return any(
        isinstance(
            current,
            (
                aiohttp.ClientConnectorError,
                aiohttp.ConnectionTimeoutError,
                SocksProxyConnectionError,
                PythonSocksProxyConnectionError,
            ),
        )
        for current in _exception_chain(exc)
    )


def is_proxy_endpoint_failure(exc: BaseException) -> bool:
    """Return whether the failed connection target is a configured proxy."""

    return any(
        isinstance(
            current,
            (aiohttp.ClientProxyConnectionError, SocksProxyConnectionError, PythonSocksProxyConnectionError),
        )
        for current in _exception_chain(exc)
    )


def process_network_error_code(
    exc: BaseException,
    *,
    fallback: str,
    include_permanent_dns: bool = True,
) -> str:
    return (
        PROCESS_NETWORK_UNAVAILABLE_CODE
        if is_process_network_failure(exc, include_permanent_dns=include_permanent_dns)
        else fallback
    )


def is_process_network_error(code: str | None) -> bool:
    return code == PROCESS_NETWORK_UNAVAILABLE_CODE


async def rotate_shared_http_transport(
    *,
    transport: str,
    request_id: str | None,
    failed_session: aiohttp.ClientSession | None = None,
) -> str:
    rotation = await refresh_http_client_after_network_failure(failed_session=failed_session)
    logger.warning(
        "process_network_recovery stage=detected transport=%s rotation=%s request_id=%s",
        transport,
        rotation,
        request_id,
    )
    return rotation


@dataclass(slots=True)
class ProcessNetworkRecovery:
    transport: str
    request_id: str | None
    account_id: str | None = None
    attempts: int = 0
    _shared_rotation_requested: bool = False
    _recovered_logged: bool = False

    async def wait(
        self,
        *,
        error_code: str | None,
        retryable_same_contract: bool,
        deadline: float,
        rotate_shared_client: bool = False,
        failed_session: aiohttp.ClientSession | None = None,
    ) -> NetworkRecoveryDecision:
        if not is_process_network_error(error_code):
            return "not_applicable"
        remaining_budget_seconds = deadline - time.monotonic()
        should_rotate = (
            rotate_shared_client
            and not self._shared_rotation_requested
            and (retryable_same_contract or failed_session is not None)
        )
        # The request deadline decides whether work may be replayed, but it
        # must not leave a concrete failed generation current for later
        # callers. Generation-less rotation remains request-budget-bound.
        must_rotate_concrete_generation = should_rotate and failed_session is not None
        if remaining_budget_seconds <= 0 and not must_rotate_concrete_generation:
            if not retryable_same_contract:
                return "not_applicable"
            self._log("exhausted", delay_seconds=0.0)
            return "exhausted"
        self._recovered_logged = False
        if retryable_same_contract:
            self.attempts += 1
        # A consumed request body cannot be replayed, but its concrete failed
        # shared generation must still be retired for subsequent callers. The
        # session identity keeps that cleanup compare-and-swap scoped.
        if should_rotate:
            try:
                if failed_session is not None:
                    async with asyncio.timeout(_CONCRETE_FAILED_GENERATION_ROTATION_TIMEOUT_SECONDS):
                        await rotate_shared_http_transport(
                            transport=self.transport,
                            request_id=self.request_id,
                            failed_session=failed_session,
                        )
                else:
                    async with asyncio.timeout_at(deadline):
                        await rotate_shared_http_transport(
                            transport=self.transport,
                            request_id=self.request_id,
                            failed_session=None,
                        )
            except TimeoutError:
                if not retryable_same_contract:
                    return "not_applicable"
                self._log("exhausted", delay_seconds=0.0)
                return "exhausted"
            except Exception:
                if retryable_same_contract:
                    raise
                logger.warning(
                    "process_network_recovery stage=rotation_failed transport=%s request_id=%s account_id=%s",
                    self.transport,
                    self.request_id,
                    self.account_id,
                    exc_info=True,
                )
                return "not_applicable"
            self._shared_rotation_requested = True
        if not retryable_same_contract:
            return "not_applicable"
        # Rotation can block on client teardown/creation. Never calculate the
        # recovery sleep from the pre-rotation budget snapshot.
        remaining_budget_seconds = deadline - time.monotonic()
        if remaining_budget_seconds <= 0:
            self._log("exhausted", delay_seconds=0.0)
            return "exhausted"
        delay = min(
            _MAX_RETRY_DELAY_SECONDS,
            backoff_seconds(self.attempts),
            remaining_budget_seconds,
        )
        self._log("retrying", delay_seconds=delay)
        # The caller's absolute request deadline owns both rotation and
        # backoff; cancellation after the budget boundary must not leak into a
        # later replay attempt.
        try:
            async with asyncio.timeout(remaining_budget_seconds):
                await asyncio.sleep(delay)
        except TimeoutError:
            self._log("exhausted", delay_seconds=0.0)
            return "exhausted"
        if deadline - time.monotonic() <= 0:
            self._log("exhausted", delay_seconds=0.0)
            return "exhausted"
        return "retry"

    def log_recovered(self) -> None:
        if self.attempts and not self._recovered_logged:
            self._log("recovered", delay_seconds=0.0)
            self._recovered_logged = True
            self._shared_rotation_requested = False

    def _log(self, stage: str, *, delay_seconds: float) -> None:
        logger.log(
            logging.INFO if stage == "recovered" else logging.WARNING,
            "process_network_recovery stage=%s transport=%s request_id=%s account_id=%s attempt=%s delay_seconds=%.2f",
            stage,
            self.transport,
            self.request_id,
            self.account_id,
            self.attempts,
            delay_seconds,
        )
