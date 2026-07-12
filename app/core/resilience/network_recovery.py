from __future__ import annotations

import asyncio
import errno
import logging
import socket
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

import aiohttp

from app.core.clients.http import refresh_http_client_after_network_failure
from app.core.utils.retry import backoff_seconds

logger = logging.getLogger(__name__)

PROCESS_NETWORK_UNAVAILABLE_CODE = "proxy_network_unavailable"

_DNS_ERROR_NUMBERS = frozenset(
    value for name in ("EAI_AGAIN", "EAI_FAIL", "EAI_NONAME") if isinstance((value := getattr(socket, name, None)), int)
)
_ROUTE_ERROR_NUMBERS = frozenset(
    value
    for name in ("ENETDOWN", "ENETUNREACH", "EHOSTDOWN", "EHOSTUNREACH", "ENONET")
    if isinstance((value := getattr(errno, name, None)), int)
)
_SERIALIZED_NETWORK_FAILURE_MARKERS = (
    "temporary failure in name resolution",
    "name or service not known",
    "nodename nor servname provided",
    "non-recoverable failure in name resolution",
    "network is down",
    "network is unreachable",
    "no route to host",
)
_MAX_RETRY_DELAY_SECONDS = 5.0

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


def is_process_network_failure(exc: BaseException) -> bool:
    """Return whether an exception represents host-wide DNS or route loss."""

    for current in _exception_chain(exc):
        if isinstance(current, socket.gaierror) and current.errno in _DNS_ERROR_NUMBERS:
            return True
        if isinstance(current, OSError) and current.errno in _ROUTE_ERROR_NUMBERS:
            return True
    return False


def is_serialized_process_network_failure(message: str | None) -> bool:
    """Recognize network-loss errors after an exception crossed an SSE boundary."""

    if not message:
        return False
    normalized = message.casefold()
    return any(marker in normalized for marker in _SERIALIZED_NETWORK_FAILURE_MARKERS)


def process_network_error_code(exc: BaseException, *, fallback: str) -> str:
    return PROCESS_NETWORK_UNAVAILABLE_CODE if is_process_network_failure(exc) else fallback


def is_process_network_error(code: str | None, message: str | None) -> bool:
    return code == PROCESS_NETWORK_UNAVAILABLE_CODE or (
        code == "upstream_unavailable" and is_serialized_process_network_failure(message)
    )


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

    async def wait(
        self,
        *,
        error_code: str | None,
        error_message: str | None,
        remaining_budget_seconds: float,
        rotate_shared_client: bool = False,
    ) -> NetworkRecoveryDecision:
        if not is_process_network_error(error_code, error_message):
            return "not_applicable"
        if remaining_budget_seconds <= 0:
            self._log("exhausted", delay_seconds=0.0)
            return "exhausted"
        self.attempts += 1
        if rotate_shared_client and not self._shared_rotation_requested:
            await rotate_shared_http_transport(
                transport=self.transport,
                request_id=self.request_id,
            )
            self._shared_rotation_requested = True
        delay = min(
            _MAX_RETRY_DELAY_SECONDS,
            backoff_seconds(self.attempts),
            remaining_budget_seconds,
        )
        self._log("retrying", delay_seconds=delay)
        await asyncio.sleep(delay)
        return "retry"

    def log_recovered(self) -> None:
        if self.attempts:
            self._log("recovered", delay_seconds=0.0)

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
