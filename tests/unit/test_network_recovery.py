from __future__ import annotations

import errno
import logging
import socket
from unittest.mock import AsyncMock

import pytest
from aiohttp.client_exceptions import ClientConnectorError
from aiohttp.client_reqrep import ConnectionKey

import app.core.resilience.network_recovery as network_recovery

pytestmark = pytest.mark.unit


def _connector_error(os_error: OSError) -> ClientConnectorError:
    key = ConnectionKey("chatgpt.com", 443, True, True, None, None, None)
    return ClientConnectorError(key, os_error)


@pytest.mark.parametrize("error_number", [socket.EAI_AGAIN, socket.EAI_FAIL, socket.EAI_NONAME])
def test_process_network_failure_classifies_dns_errors(error_number: int) -> None:
    assert network_recovery.is_process_network_failure(socket.gaierror(error_number, "DNS failure"))


def test_process_network_failure_inspects_aiohttp_embedded_os_error() -> None:
    error = _connector_error(socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution"))

    assert network_recovery.is_process_network_failure(error)
    assert (
        network_recovery.process_network_error_code(error, fallback="upstream_unavailable")
        == network_recovery.PROCESS_NETWORK_UNAVAILABLE_CODE
    )


@pytest.mark.parametrize("error_number", [errno.ENETDOWN, errno.ENETUNREACH, errno.EHOSTUNREACH])
def test_process_network_failure_classifies_host_route_errors(error_number: int) -> None:
    assert network_recovery.is_process_network_failure(OSError(error_number, "route failure"))


@pytest.mark.parametrize(
    "error",
    [
        ConnectionRefusedError(errno.ECONNREFUSED, "refused"),
        ConnectionResetError(errno.ECONNRESET, "reset"),
        TimeoutError("timed out"),
    ],
)
def test_process_network_failure_does_not_classify_endpoint_failures(error: OSError) -> None:
    assert not network_recovery.is_process_network_failure(error)


@pytest.mark.parametrize(
    "message",
    [
        "Temporary failure in name resolution",
        "Name or service not known",
        "nodename nor servname provided, or not known",
        "Network is unreachable",
        "No route to host",
    ],
)
def test_serialized_process_network_failure_markers(message: str) -> None:
    assert network_recovery.is_process_network_error("upstream_unavailable", message)


def test_serialized_process_network_failure_requires_network_error_code() -> None:
    assert not network_recovery.is_process_network_error("invalid_api_key", "Network is unreachable")


@pytest.mark.asyncio
async def test_recovery_controller_retries_and_logs_recovery(monkeypatch, caplog) -> None:
    sleep = AsyncMock()
    rotate = AsyncMock(return_value="rotated")
    monkeypatch.setattr(network_recovery.asyncio, "sleep", sleep)
    monkeypatch.setattr(network_recovery, "rotate_shared_http_transport", rotate)
    recovery = network_recovery.ProcessNetworkRecovery(
        transport="websocket",
        request_id="req_network_recovery",
        account_id="acc_1",
    )

    with caplog.at_level(logging.INFO, logger=network_recovery.__name__):
        first = await recovery.wait(
            error_code=network_recovery.PROCESS_NETWORK_UNAVAILABLE_CODE,
            error_message="DNS failure",
            remaining_budget_seconds=10.0,
            rotate_shared_client=True,
        )
        second = await recovery.wait(
            error_code=network_recovery.PROCESS_NETWORK_UNAVAILABLE_CODE,
            error_message="DNS failure",
            remaining_budget_seconds=10.0,
            rotate_shared_client=True,
        )
        recovery.log_recovered()

    assert first == second == "retry"
    assert sleep.await_count == 2
    rotate.assert_awaited_once_with(transport="websocket", request_id="req_network_recovery")
    assert "stage=retrying" in caplog.text
    assert "stage=recovered" in caplog.text
    assert "account_id=acc_1" in caplog.text


@pytest.mark.asyncio
async def test_recovery_controller_is_bounded_by_remaining_budget(monkeypatch) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr(network_recovery.asyncio, "sleep", sleep)
    recovery = network_recovery.ProcessNetworkRecovery(transport="stream", request_id="req_bounded")

    decision = await recovery.wait(
        error_code=network_recovery.PROCESS_NETWORK_UNAVAILABLE_CODE,
        error_message=None,
        remaining_budget_seconds=0.0,
    )

    assert decision == "exhausted"
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_controller_ignores_other_failures(monkeypatch) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr(network_recovery.asyncio, "sleep", sleep)
    recovery = network_recovery.ProcessNetworkRecovery(transport="stream", request_id="req_other")

    decision = await recovery.wait(
        error_code="upstream_unavailable",
        error_message="Connection refused",
        remaining_budget_seconds=10.0,
    )

    assert decision == "not_applicable"
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_rotation_diagnostic_identifies_already_rotated_generation(monkeypatch, caplog) -> None:
    refresh = AsyncMock(return_value="already_rotated")
    monkeypatch.setattr(network_recovery, "refresh_http_client_after_network_failure", refresh)

    with caplog.at_level(logging.WARNING, logger=network_recovery.__name__):
        result = await network_recovery.rotate_shared_http_transport(
            transport="http",
            request_id="req_coalesced",
        )

    assert result == "already_rotated"
    assert "stage=detected" in caplog.text
    assert "rotation=already_rotated" in caplog.text
    assert "request_id=req_coalesced" in caplog.text
