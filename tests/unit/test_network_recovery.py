from __future__ import annotations

import asyncio
import errno
import logging
import socket
from typing import cast
from unittest.mock import AsyncMock

import aiohttp
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


def test_routed_classifier_does_not_treat_missing_proxy_hostname_as_process_outage() -> None:
    error = socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    assert network_recovery.is_process_network_failure(error)
    assert not network_recovery.is_process_network_failure(error, include_permanent_dns=False)


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


def test_process_network_error_requires_stable_code_not_message_text() -> None:
    assert network_recovery.is_process_network_error(network_recovery.PROCESS_NETWORK_UNAVAILABLE_CODE)
    assert not network_recovery.is_process_network_error("upstream_unavailable")


@pytest.mark.asyncio
async def test_recovery_controller_retries_and_logs_recovery(monkeypatch, caplog) -> None:
    sleep = AsyncMock()
    rotate = AsyncMock(return_value="rotated")
    monkeypatch.setattr(network_recovery.asyncio, "sleep", sleep)
    monkeypatch.setattr(network_recovery, "rotate_shared_http_transport", rotate)
    monkeypatch.setattr(network_recovery.time, "monotonic", lambda: 100.0)
    recovery = network_recovery.ProcessNetworkRecovery(
        transport="websocket",
        request_id="req_network_recovery",
        account_id="acc_1",
    )
    failed_session = cast(aiohttp.ClientSession, object())

    with caplog.at_level(logging.INFO, logger=network_recovery.__name__):
        first = await recovery.wait(
            error_code=network_recovery.PROCESS_NETWORK_UNAVAILABLE_CODE,
            retryable_same_contract=True,
            deadline=110.0,
            rotate_shared_client=True,
            failed_session=failed_session,
        )
        second = await recovery.wait(
            error_code=network_recovery.PROCESS_NETWORK_UNAVAILABLE_CODE,
            retryable_same_contract=True,
            deadline=110.0,
            rotate_shared_client=True,
        )
        recovery.log_recovered()

    assert first == second == "retry"
    assert sleep.await_count == 2
    rotate.assert_awaited_once_with(
        transport="websocket",
        request_id="req_network_recovery",
        failed_session=failed_session,
    )
    assert "stage=retrying" in caplog.text
    assert "stage=recovered" in caplog.text
    assert "account_id=acc_1" in caplog.text


@pytest.mark.asyncio
async def test_recovery_controller_is_bounded_by_remaining_budget(monkeypatch) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr(network_recovery.asyncio, "sleep", sleep)
    monkeypatch.setattr(network_recovery.time, "monotonic", lambda: 100.0)
    recovery = network_recovery.ProcessNetworkRecovery(transport="stream", request_id="req_bounded")

    decision = await recovery.wait(
        error_code=network_recovery.PROCESS_NETWORK_UNAVAILABLE_CODE,
        retryable_same_contract=True,
        deadline=100.0,
    )

    assert decision == "exhausted"
    sleep.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("retryable_same_contract", "expected_decision"),
    [(False, "not_applicable"), (True, "exhausted")],
    ids=["unsafe", "retryable"],
)
async def test_recovery_controller_rotates_concrete_failed_generation_after_deadline(
    monkeypatch,
    retryable_same_contract: bool,
    expected_decision: network_recovery.NetworkRecoveryDecision,
) -> None:
    sleep = AsyncMock()
    rotate = AsyncMock(return_value="rotated")
    failed_session = cast(aiohttp.ClientSession, object())
    monkeypatch.setattr(network_recovery.asyncio, "sleep", sleep)
    monkeypatch.setattr(network_recovery, "rotate_shared_http_transport", rotate)
    monkeypatch.setattr(network_recovery.time, "monotonic", lambda: 100.0)
    recovery = network_recovery.ProcessNetworkRecovery(transport="compact", request_id="req_expired_rotation")

    decision = await recovery.wait(
        error_code=network_recovery.PROCESS_NETWORK_UNAVAILABLE_CODE,
        retryable_same_contract=retryable_same_contract,
        deadline=99.0,
        rotate_shared_client=True,
        failed_session=failed_session,
    )

    assert decision == expected_decision
    rotate.assert_awaited_once_with(
        transport="compact",
        request_id="req_expired_rotation",
        failed_session=failed_session,
    )
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_controller_ignores_other_failures(monkeypatch) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr(network_recovery.asyncio, "sleep", sleep)
    recovery = network_recovery.ProcessNetworkRecovery(transport="stream", request_id="req_other")

    decision = await recovery.wait(
        error_code="upstream_unavailable",
        retryable_same_contract=True,
        deadline=110.0,
    )

    assert decision == "not_applicable"
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_controller_rejects_unsafe_network_replay_without_failed_generation(monkeypatch) -> None:
    sleep = AsyncMock()
    rotate = AsyncMock(return_value="rotated")
    monkeypatch.setattr(network_recovery.asyncio, "sleep", sleep)
    monkeypatch.setattr(network_recovery, "rotate_shared_http_transport", rotate)
    recovery = network_recovery.ProcessNetworkRecovery(transport="stream", request_id="req_unsafe")

    decision = await recovery.wait(
        error_code=network_recovery.PROCESS_NETWORK_UNAVAILABLE_CODE,
        retryable_same_contract=False,
        deadline=network_recovery.time.monotonic() + 10.0,
        rotate_shared_client=True,
    )

    assert decision == "not_applicable"
    rotate.assert_not_awaited()
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_controller_rotates_concrete_failed_generation_without_replaying(monkeypatch) -> None:
    sleep = AsyncMock()
    rotate = AsyncMock(return_value="rotated")
    failed_session = cast(aiohttp.ClientSession, object())
    monkeypatch.setattr(network_recovery.asyncio, "sleep", sleep)
    monkeypatch.setattr(network_recovery, "rotate_shared_http_transport", rotate)
    recovery = network_recovery.ProcessNetworkRecovery(transport="stream", request_id="req_unsafe_rotation")

    decision = await recovery.wait(
        error_code=network_recovery.PROCESS_NETWORK_UNAVAILABLE_CODE,
        retryable_same_contract=False,
        deadline=network_recovery.time.monotonic() + 10.0,
        rotate_shared_client=True,
        failed_session=failed_session,
    )

    assert decision == "not_applicable"
    rotate.assert_awaited_once_with(
        transport="stream",
        request_id="req_unsafe_rotation",
        failed_session=failed_session,
    )
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_unsafe_recovery_rotation_timeout_preserves_original_failure(monkeypatch) -> None:
    async def blocked_rotation(**_kwargs: object) -> str:
        await asyncio.Event().wait()
        return "unreachable"  # pragma: no cover

    sleep = AsyncMock()
    monkeypatch.setattr(network_recovery.asyncio, "sleep", sleep)
    monkeypatch.setattr(network_recovery, "rotate_shared_http_transport", blocked_rotation)
    monkeypatch.setattr(network_recovery, "_CONCRETE_FAILED_GENERATION_ROTATION_TIMEOUT_SECONDS", 0.01)
    recovery = network_recovery.ProcessNetworkRecovery(transport="stream", request_id="req_unsafe_timeout")

    decision = await recovery.wait(
        error_code=network_recovery.PROCESS_NETWORK_UNAVAILABLE_CODE,
        retryable_same_contract=False,
        deadline=network_recovery.time.monotonic() + 0.01,
        rotate_shared_client=True,
        failed_session=cast(aiohttp.ClientSession, object()),
    )

    assert decision == "not_applicable"
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_controller_bounds_rotation_by_deadline(monkeypatch) -> None:
    async def blocked_rotation(**_kwargs: object) -> str:
        await asyncio.Event().wait()
        return "unreachable"  # pragma: no cover

    monkeypatch.setattr(network_recovery, "rotate_shared_http_transport", blocked_rotation)
    recovery = network_recovery.ProcessNetworkRecovery(transport="stream", request_id="req_rotation_timeout")

    decision = await recovery.wait(
        error_code=network_recovery.PROCESS_NETWORK_UNAVAILABLE_CODE,
        retryable_same_contract=True,
        deadline=network_recovery.time.monotonic() + 0.01,
        rotate_shared_client=True,
    )

    assert decision == "exhausted"


@pytest.mark.asyncio
async def test_recovery_controller_recomputes_deadline_after_rotation(monkeypatch) -> None:
    now = [100.0]

    async def rotate(**_kwargs: object) -> str:
        now[0] = 110.0
        return "rotated"

    sleep = AsyncMock()
    monkeypatch.setattr(network_recovery.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(network_recovery.asyncio, "sleep", sleep)
    monkeypatch.setattr(network_recovery, "rotate_shared_http_transport", rotate)
    recovery = network_recovery.ProcessNetworkRecovery(transport="websocket", request_id="req_rotation_deadline")

    decision = await recovery.wait(
        error_code=network_recovery.PROCESS_NETWORK_UNAVAILABLE_CODE,
        retryable_same_contract=True,
        deadline=110.0,
        rotate_shared_client=True,
    )

    assert decision == "exhausted"
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
