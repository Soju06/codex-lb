from __future__ import annotations

import json
import logging
from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.modules.telemetry.consent import TelemetryIdentity
from app.modules.telemetry.schemas import TelemetrySnapshot, build_snapshot_envelope
from app.modules.telemetry.sender import TelemetrySender

pytestmark = pytest.mark.unit


class _FakeResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def read(self) -> bytes:
        return b""


class _FakeClientSession:
    def __init__(self) -> None:
        self.requests: list[tuple[str, bytes, dict[str, str]]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    def post(self, url: str, *, data: bytes, headers: dict[str, str]) -> _FakeResponse:
        self.requests.append((url, data, headers))
        return _FakeResponse()


def _snapshot() -> TelemetrySnapshot:
    return TelemetrySnapshot.model_validate(
        {
            "consent": "enabled",
            "instance_id": "00000000-0000-4000-8000-000000000004",
            "version": "1.0.0",
            "python": "3.13",
            "os": "linux",
            "arch": "x86_64",
            "uptime_hours": 1,
            "deploy": {
                "method": "bare",
                "db_backend": "sqlite",
                "db_size_bucket": "<100MB",
                "replicas": 1,
                "reverse_proxy": False,
            },
            "accounts": {
                "pool_bucket": "0",
                "plan_mix": {"plus": "0", "pro": "0", "team": "0", "free": "0"},
                "workspace_accounts": False,
                "routing_policy": "capacity_weighted",
                "limit_warmup_enabled": False,
                "egress_proxy_used": False,
            },
            "usage_7d": {
                "requests": 0,
                "success_rate": 0,
                "tokens_input": 0,
                "tokens_output": 0,
                "tokens_cached_ratio": 0,
                "cost_usd_bucket": "<10",
                "request_kinds": {"responses": 0, "chat": 0, "images": 0, "unknown": 0},
                "transport_mix": {"ws": 0, "http_bridge": 0},
                "service_tier_mix": {"default": 0, "flex": 0, "priority": 0},
                "clients": {},
                "clients_other_ratio": 0,
                "models": [],
                "latency_ms_p50": 0,
                "ttft_ms_p50": 0,
                "ttft_ms_p95": 0,
                "rate_limit_429_ratio": 0,
                "top_upstream_errors": [],
            },
            "features": {
                "api_firewall": False,
                "quota_planner": False,
                "sticky_sessions": False,
                "conversation_archive": False,
                "automations": False,
                "fleet": True,
                "model_sources_count": 0,
                "api_keys_bucket": "0",
                "prometheus": False,
                "otel": False,
                "dashboard_auth": True,
                "reset_credits": False,
                "image_api_used": False,
            },
        }
    )


@pytest.mark.asyncio
async def test_sender_failure_isolated_retries_once_and_logs_debug_only(caplog) -> None:
    snapshot = _snapshot()
    identity = TelemetryIdentity(snapshot.instance_id, Ed25519PrivateKey.generate())

    async def context_provider():
        return True, identity

    sender = TelemetrySender("http://127.0.0.1:1", context_provider=context_provider)
    sender._transmit_once = AsyncMock(side_effect=OSError("endpoint unreachable"))

    with caplog.at_level(logging.DEBUG, logger="app.modules.telemetry.sender"):
        await sender.send_snapshot(snapshot)

    assert sender._transmit_once.await_count == 2
    assert caplog.records
    assert all(record.levelno == logging.DEBUG for record in caplog.records)


@pytest.mark.asyncio
async def test_sender_disabled_guard_does_not_construct_http_client(monkeypatch) -> None:
    async def context_provider():
        return False, None

    client_session = Mock()
    monkeypatch.setattr("app.modules.telemetry.sender.aiohttp.ClientSession", client_session)

    await TelemetrySender(context_provider=context_provider).send_snapshot(_snapshot())

    client_session.assert_not_called()


@pytest.mark.asyncio
async def test_sender_aborts_snapshot_when_consent_becomes_inactive_before_post(monkeypatch) -> None:
    snapshot = _snapshot()
    identity = TelemetryIdentity(snapshot.instance_id, Ed25519PrivateKey.generate())
    context_provider = AsyncMock(side_effect=[(True, identity), (False, None)])
    session = _FakeClientSession()
    monkeypatch.setattr("app.modules.telemetry.sender.aiohttp.ClientSession", Mock(return_value=session))

    await TelemetrySender(
        "https://telemetry.example",
        context_provider=context_provider,
    ).send_snapshot(snapshot)

    assert [request[0] for request in session.requests] == [
        "https://telemetry.example/v1/register",
        "https://telemetry.example/v1/activate",
    ]
    assert context_provider.await_count == 2


@pytest.mark.asyncio
async def test_sender_posts_snapshot_once_when_consent_stays_active(monkeypatch) -> None:
    snapshot = _snapshot()
    identity = TelemetryIdentity(snapshot.instance_id, Ed25519PrivateKey.generate())
    context_provider = AsyncMock(return_value=(True, identity))
    session = _FakeClientSession()
    monkeypatch.setattr("app.modules.telemetry.sender.aiohttp.ClientSession", Mock(return_value=session))

    await TelemetrySender(
        "https://telemetry.example",
        context_provider=context_provider,
    ).send_snapshot(snapshot)

    assert [request[0] for request in session.requests] == [
        "https://telemetry.example/v1/register",
        "https://telemetry.example/v1/activate",
        "https://telemetry.example/v1/snapshot",
    ]
    assert context_provider.await_count == 2


@pytest.mark.asyncio
async def test_sender_aborts_snapshot_when_identity_changes_before_post(monkeypatch) -> None:
    snapshot = _snapshot()
    identity = TelemetryIdentity(snapshot.instance_id, Ed25519PrivateKey.generate())
    replacement_identity = TelemetryIdentity(snapshot.instance_id, Ed25519PrivateKey.generate())
    context_provider = AsyncMock(side_effect=[(True, identity), (True, replacement_identity)])
    session = _FakeClientSession()
    monkeypatch.setattr("app.modules.telemetry.sender.aiohttp.ClientSession", Mock(return_value=session))

    await TelemetrySender(
        "https://telemetry.example",
        context_provider=context_provider,
    ).send_snapshot(snapshot)

    assert [request[0] for request in session.requests] == [
        "https://telemetry.example/v1/register",
        "https://telemetry.example/v1/activate",
    ]
    assert context_provider.await_count == 2


@pytest.mark.asyncio
async def test_sender_aborts_snapshot_when_consent_recheck_fails(monkeypatch, caplog) -> None:
    snapshot = _snapshot()
    identity = TelemetryIdentity(snapshot.instance_id, Ed25519PrivateKey.generate())
    context_provider = AsyncMock(side_effect=[(True, identity), OSError("database unavailable")])
    session = _FakeClientSession()
    monkeypatch.setattr("app.modules.telemetry.sender.aiohttp.ClientSession", Mock(return_value=session))

    with caplog.at_level(logging.DEBUG, logger="app.modules.telemetry.sender"):
        await TelemetrySender(
            "https://telemetry.example",
            context_provider=context_provider,
        ).send_snapshot(snapshot)

    assert [request[0] for request in session.requests] == [
        "https://telemetry.example/v1/register",
        "https://telemetry.example/v1/activate",
    ]
    assert context_provider.await_count == 2
    assert [record.message for record in caplog.records] == ["Anonymous telemetry consent re-check failed"]


@pytest.mark.asyncio
async def test_opt_out_with_inactive_consent_registers_activates_and_posts_exact_signed_canonical_body(
    monkeypatch,
) -> None:
    identity = TelemetryIdentity("00000000-0000-4000-8000-000000000004", Ed25519PrivateKey.generate())
    session = _FakeClientSession()
    client_session = Mock(return_value=session)
    context_provider = AsyncMock(return_value=(False, None))
    monkeypatch.setattr("app.modules.telemetry.sender.aiohttp.ClientSession", client_session)
    monkeypatch.setattr("app.modules.telemetry.sender.utcnow", lambda: datetime(2026, 8, 20, 12, 0, 0))

    await TelemetrySender(
        "https://telemetry.example",
        context_provider=context_provider,
    ).send_opt_out(
        identity,
        app_version="1.24.0",
        deployment_mode="docker",
        os_arch="linux/x86_64",
    )

    assert [request[0] for request in session.requests] == [
        "https://telemetry.example/v1/register",
        "https://telemetry.example/v1/activate",
        "https://telemetry.example/v1/optout",
    ]
    expected_body = (
        b'{"app_version":"1.24.0","event":"optout",'
        b'"instance_id":"00000000-0000-4000-8000-000000000004",'
        b'"occurred_at":"2026-08-20T12:00:00Z"}'
    )
    _, body, headers = session.requests[-1]
    assert body == expected_body
    assert headers["X-Instance-ID"] == identity.instance_id
    identity.private_key.public_key().verify(bytes.fromhex(headers["X-Signature"]), body)
    context_provider.assert_not_awaited()
    client_session.assert_called_once()
    assert client_session.call_args.kwargs["timeout"].total == 5.0
    assert client_session.call_args.kwargs["trust_env"] is False


@pytest.mark.asyncio
async def test_opt_out_retries_once_then_succeeds(monkeypatch) -> None:
    identity = TelemetryIdentity("00000000-0000-4000-8000-000000000004", Ed25519PrivateKey.generate())
    session = _FakeClientSession()
    monkeypatch.setattr("app.modules.telemetry.sender.aiohttp.ClientSession", Mock(return_value=session))
    sender = TelemetrySender()
    sender._transmit_opt_out_once = AsyncMock(side_effect=[OSError("transient"), None])

    await sender.send_opt_out(
        identity,
        app_version="1.24.0",
        deployment_mode="bare",
        os_arch="linux/x86_64",
    )

    assert sender._transmit_opt_out_once.await_count == 2


@pytest.mark.asyncio
async def test_opt_out_failure_is_swallowed_and_logged_at_debug(monkeypatch, caplog) -> None:
    identity = TelemetryIdentity("00000000-0000-4000-8000-000000000004", Ed25519PrivateKey.generate())
    session = _FakeClientSession()
    monkeypatch.setattr("app.modules.telemetry.sender.aiohttp.ClientSession", Mock(return_value=session))
    sender = TelemetrySender()
    sender._transmit_opt_out_once = AsyncMock(side_effect=OSError("collector unavailable"))

    with caplog.at_level(logging.DEBUG, logger="app.modules.telemetry.sender"):
        await sender.send_opt_out(
            identity,
            app_version="1.24.0",
            deployment_mode="bare",
            os_arch="linux/x86_64",
        )

    assert sender._transmit_opt_out_once.await_count == 2
    assert caplog.records
    assert all(record.levelno == logging.DEBUG for record in caplog.records)


@pytest.mark.asyncio
async def test_sender_uses_canonical_shm_paths_and_valid_ed25519_signature() -> None:
    snapshot = _snapshot()
    identity = TelemetryIdentity(snapshot.instance_id, Ed25519PrivateKey.generate())
    sender = TelemetrySender(context_provider=AsyncMock(return_value=(True, identity)))
    sender._post = AsyncMock()
    sender._post_signed = AsyncMock()
    session = Mock()

    await sender._transmit_once(session, snapshot, identity)

    register_call = sender._post.await_args
    assert register_call is not None
    assert register_call.args[1] == "/v1/register"
    assert [call.args[1] for call in sender._post_signed.await_args_list] == ["/v1/activate", "/v1/snapshot"]
    assert all("/api/v1/" not in call.args[1] for call in sender._post_signed.await_args_list)

    registration = json.loads(register_call.args[2])
    activation = json.loads(sender._post_signed.await_args_list[0].args[2])
    envelope = json.loads(sender._post_signed.await_args_list[1].args[2])
    assert set(registration) == {
        "app_name",
        "app_version",
        "deployment_mode",
        "environment",
        "instance_id",
        "os_arch",
        "public_key",
    }
    assert set(activation) == {"action"}
    assert set(envelope) == {"instance_id", "metrics", "timestamp"}

    signing_sender = TelemetrySender()
    signing_sender._post = AsyncMock()
    body = b'{"action":"activate"}'
    await signing_sender._post_signed(session, "/v1/activate", body, identity, accepted={200})
    signed_call = signing_sender._post.await_args
    assert signed_call is not None
    headers = signed_call.kwargs["headers"]
    identity.private_key.public_key().verify(bytes.fromhex(headers["X-Signature"]), body)
    assert headers["X-Instance-ID"] == identity.instance_id


def _key_structure(value):
    if isinstance(value, dict):
        return {key: _key_structure(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_key_structure(value[0])] if value else []
    return None


@pytest.mark.asyncio
async def test_preview_and_sender_snapshot_envelopes_have_identical_key_structure() -> None:
    snapshot = _snapshot()
    identity = TelemetryIdentity(snapshot.instance_id, Ed25519PrivateKey.generate())
    preview = build_snapshot_envelope(snapshot)
    sender = TelemetrySender(context_provider=AsyncMock(return_value=(True, identity)))
    sender._post = AsyncMock()
    sender._post_signed = AsyncMock()

    await sender._transmit_once(Mock(), snapshot, identity)

    sender_body = json.loads(sender._post_signed.await_args_list[-1].args[2])
    preview_body = json.loads(preview.model_dump_json())
    assert _key_structure(sender_body) == _key_structure(preview_body)
