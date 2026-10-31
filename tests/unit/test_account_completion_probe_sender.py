from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.clients import proxy as proxy_client
from app.core.clients.codex import CodexTransportError
from app.core.crypto import TokenEncryptor
from app.core.openai.requests import _UNSUPPORTED_UPSTREAM_FIELDS
from app.core.upstream_proxy import ResolvedProxyEndpoint, ResolvedUpstreamRoute, UpstreamProxyRouteError
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.modules.accounts import probe_sender as sender

pytestmark = pytest.mark.unit


def _event(kind: str, *, code: str = "secret-value", message: str = "Bearer raw-upstream-content") -> bytes:
    error = {"code": code, "message": message}
    payload = {"type": kind}
    if kind == "error":
        payload["error"] = error
    else:
        payload["response"] = {
            "id": "resp_test",
            "status": kind.removeprefix("response."),
            "output": [],
            "error": error,
        }
    return ("data: " + json.dumps(payload) + "\r\n\r\n").encode()


class _Response:
    def __init__(self, chunks: list[bytes], *, status: int = 200, stall: bool = False):
        self.status = status
        self.headers = {"Content-Type": "text/event-stream"}
        self.reason = None
        self.chunks = chunks
        self.stall = stall
        self.closed = False
        self.content = self

    async def iter_chunked(self, size):
        del size
        for chunk in self.chunks:
            yield chunk
        if self.stall:
            await asyncio.Event().wait()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    def release(self):
        self.closed = True


@pytest.fixture
def completion_probe(monkeypatch):
    account = Account(
        id="account-under-test",
        chatgpt_account_id="upstream-under-test",
        email="probe@example.invalid",
        plan_type="plus",
        status=AccountStatus.ACTIVE,
        last_refresh=utcnow(),
        access_token_encrypted=b"unused",
        refresh_token_encrypted=b"unused",
        id_token_encrypted=b"unused",
    )
    route_resolver = AsyncMock(return_value=None)

    @asynccontextmanager
    async def _background_session():
        yield SimpleNamespace()

    monkeypatch.setattr(proxy_client, "discover_native_egress_client", lambda: None)
    monkeypatch.setattr(sender, "get_background_session", _background_session)
    monkeypatch.setattr(sender, "resolve_upstream_route", route_resolver)

    def configure(response):
        post = Mock(return_value=response)

        @asynccontextmanager
        async def _lease(*args):
            yield SimpleNamespace(post=post)

        monkeypatch.setattr(proxy_client, "lease_http_session", _lease)
        return post

    async def send():
        return await sender.send_completion_probe(
            account, access_token="synthetic-test-token", model="gpt-test", encryptor=TokenEncryptor()
        )

    return account, route_resolver, configure, send


async def test_fragmented_completion_uses_fixed_prompt_and_closes_response(completion_probe):
    account, resolver, configure, send = completion_probe
    body = _event("response.created") + _event("response.completed")
    response = _Response([body[index : index + 3] for index in range(0, len(body), 3)])
    post = configure(response)

    result = await send()

    assert result.outcome == "completed"
    assert result.http_status == 200
    assert result.latency_ms >= 0
    assert result.error_message is None
    assert response.closed
    resolver.assert_awaited_once()
    assert resolver.call_args.kwargs["account_id"] == account.id
    post.assert_called_once()
    request = post.call_args.kwargs
    headers = {key.lower(): value for key, value in request["headers"].items()}
    assert headers["chatgpt-account-id"] == account.chatgpt_account_id
    assert request["json"]["model"] == "gpt-test"
    assert not (_UNSUPPORTED_UPSTREAM_FIELDS & request["json"].keys())
    assert request["json"]["input"] == [{"role": "user", "content": [{"type": "input_text", "text": "."}]}]
    assert request["json"]["store"] is False
    assert "reasoning" not in request["json"]
    assert "text" not in request["json"]
    assert headers.get("accept-encoding", "identity") == "identity"
    assert "Accept-Encoding" in request["skip_auto_headers"]
    assert headers["user-agent"].startswith("codex_cli_rs/")


@pytest.mark.parametrize(
    "kind,outcome",
    [
        ("response.failed", "failed"),
        ("error", "failed"),
        ("response.incomplete", "incomplete"),
    ],
)
async def test_http_200_error_terminal_is_not_success(completion_probe, kind, outcome):
    _, _, configure, send = completion_probe
    response = _Response([_event(kind)])
    configure(response)

    result = await send()

    assert result.http_status == 200
    assert result.outcome == outcome
    assert result.error_code
    assert "secret-value" not in repr(result)
    assert "raw-upstream-content" not in repr(result)
    assert response.closed


@pytest.mark.parametrize("kind", ["response.failed", "error"])
@pytest.mark.parametrize("code", ["token_expired", "usage_limit_reached", "model_not_found", "content_filter"])
async def test_terminal_preserves_actionable_error_code(completion_probe, kind, code):
    _, _, configure, send = completion_probe
    response = _Response([_event(kind, code=code, message="The selected account test was rejected.")])
    configure(response)
    result = await send()
    assert result.http_status == 200
    assert result.outcome == "failed"
    assert result.error_code == code
    assert result.error_message == "The selected account test was rejected."
    assert response.closed


async def test_terminal_message_redacts_credentials_before_truncation(completion_probe):
    _, _, configure, send = completion_probe
    secrets = [
        "synthetic-test-token",
        "synthetic-bearer",
        "synthetic-keyed",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.synthetic-signature",
        "sk-test-synthetic-secret",
        "proxy.invalid",
    ]
    message = (
        f"Expired {secrets[0]} Bearer {secrets[1]} token={secrets[2]} {secrets[3]} {secrets[4]} "
        f"https://user:password@{secrets[5]}/path\n" + "details " * 200
    )
    configure(_Response([_event("response.failed", code="token_expired", message=message)]))
    result = await send()
    assert result.error_code == "token_expired"
    assert result.error_message.startswith("Expired [REDACTED]")
    assert len(result.error_message) == sender.COMPLETION_MAX_ERROR_MESSAGE_CHARS
    assert "\n" not in result.error_message
    assert all(secret not in result.error_message for secret in secrets)


@pytest.mark.parametrize("code", ["Bearer secret", "x" * 65, "synthetic-test-token", "sk-test-secret"])
async def test_untrusted_error_code_uses_fixed_fallback(completion_probe, code):
    _, _, configure, send = completion_probe
    configure(_Response([_event("response.failed", code=code)]))
    result = await send()
    assert result.error_code == "response_failed"


async def test_http_error_reads_fragmented_structured_code_with_redaction(completion_probe):
    _, _, configure, send = completion_probe
    data = json.dumps(
        {"error": {"code": "usage_limit_reached", "message": "Quota exceeded. Bearer secret-http"}}
    ).encode()
    response = _Response([data[index : index + 5] for index in range(0, len(data), 5)], status=429)
    configure(response)
    result = await send()
    assert result.http_status == 429
    assert result.outcome == "failed"
    assert result.error_code == "usage_limit_reached"
    assert result.error_message == "Quota exceeded. Bearer [REDACTED]"
    assert response.closed


@pytest.mark.parametrize(
    "body",
    [b"<html>raw proxy output</html>", b"x" * (sender.COMPLETION_MAX_ERROR_BYTES + 1)],
    ids=["unstructured", "oversized"],
)
async def test_http_error_body_is_bounded_and_never_exposes_unstructured_output(completion_probe, body):
    _, _, configure, send = completion_probe
    response = _Response([body], status=503)
    configure(response)
    result = await send()
    assert result.http_status == 503
    assert result.outcome == "failed"
    assert result.error_code == "upstream_http_error"
    assert "Upstream" in result.error_message
    assert response.closed


async def test_http_error_read_timeout_preserves_failure_status(completion_probe, monkeypatch):
    _, _, configure, send = completion_probe
    response = _Response([], status=403, stall=True)
    configure(response)
    monkeypatch.setattr(sender, "COMPLETION_IDLE_TIMEOUT_SECONDS", 0.01)
    result = await send()
    assert result.http_status == 403
    assert result.outcome == "failed"
    assert result.error_code == "upstream_http_error"
    assert response.closed


async def test_direct_native_egress_uses_normal_identity_and_wire_payload(completion_probe, monkeypatch):
    account, _, configure, send = completion_probe
    account.codex_installation_id = "probe-test-installation"
    post = configure(_Response([]))
    response = _Response([_event("response.completed")])
    native = SimpleNamespace(request=AsyncMock(return_value=response))
    monkeypatch.setattr(proxy_client, "discover_native_egress_client", lambda: native)
    result = await send()
    assert result.outcome == "completed"
    assert result.http_status == 200
    post.assert_not_called()
    native.request.assert_awaited_once()
    request = native.request.call_args.args[0]
    headers = {key.lower(): value for key, value in request.headers.items()}
    assert headers["chatgpt-account-id"] == account.chatgpt_account_id
    assert headers["user-agent"].startswith("codex_cli_rs/")
    wire = json.loads(request.body)
    assert not (_UNSUPPORTED_UPSTREAM_FIELDS & wire.keys())
    assert wire["client_metadata"]["x-codex-installation-id"] == account.codex_installation_id
    assert request.sse.max_event_bytes == sender.COMPLETION_MAX_EVENT_BYTES
    assert response.closed


async def test_stream_close_without_completion_is_incomplete(completion_probe):
    _, _, configure, send = completion_probe
    response = _Response([_event("response.created")])
    configure(response)
    result = await send()
    assert result.outcome == "incomplete"
    assert result.error_code == "stream_incomplete"
    assert result.http_status == 200
    assert response.closed


@pytest.mark.parametrize("status", [204, 302, 403, 429, 503])
async def test_actual_http_status_is_preserved(completion_probe, status):
    _, _, configure, send = completion_probe
    response = _Response([], status=status)
    configure(response)
    result = await send()
    assert result.http_status == status
    assert result.outcome == ("incomplete" if status == 204 else "failed")
    assert response.closed


async def test_timeout_retains_http_status_and_closes_response(completion_probe, monkeypatch):
    _, _, configure, send = completion_probe
    response = _Response([_event("response.created")], stall=True)
    configure(response)
    monkeypatch.setattr(sender, "COMPLETION_TIMEOUT_SECONDS", 0.02)
    result = await send()
    assert result.outcome == "incomplete"
    assert result.error_code == "probe_timeout"
    assert result.http_status == 200
    assert response.closed


async def test_oversized_event_is_bounded_and_closed(completion_probe):
    _, _, configure, send = completion_probe
    response = _Response([b"data: " + b"x" * (sender.COMPLETION_MAX_EVENT_BYTES + 1)])
    configure(response)
    result = await send()
    assert result.outcome == "incomplete"
    assert result.error_code == "probe_stream_limit"
    assert response.closed


async def test_cancellation_propagates_and_closes_response(completion_probe):
    _, _, configure, send = completion_probe
    response = _Response([], stall=True)
    configure(response)
    task = asyncio.create_task(send())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert response.closed


async def test_bound_proxy_uses_same_account_and_releases_routed_response(completion_probe, monkeypatch):
    account, resolver, configure, send = completion_probe
    post = configure(_Response([]))
    route = ResolvedUpstreamRoute(
        "account_bound", "pool", ResolvedProxyEndpoint("endpoint", "https", "proxy.invalid", 443)
    )
    resolver.return_value = route
    response = _Response([_event("response.completed")])
    client = SimpleNamespace(request=AsyncMock(return_value=response), close=AsyncMock())
    monkeypatch.setattr(proxy_client, "create_codex_session", Mock(return_value=object()))
    monkeypatch.setattr(proxy_client, "CodexClient", Mock(return_value=client))

    result = await send()

    assert result.outcome == "completed"
    client.request.assert_awaited_once()
    assert client.request.call_args.kwargs["route"] is route
    headers = {key.lower(): value for key, value in client.request.call_args.kwargs["headers"].items()}
    assert headers["chatgpt-account-id"] == account.chatgpt_account_id
    assert client.request.call_args.kwargs["buffer_response"] is False
    post.assert_not_called()
    client.close.assert_awaited_once()
    assert response.closed


async def test_route_refusal_never_sends_or_falls_back(completion_probe):
    _, resolver, configure, send = completion_probe
    post = configure(_Response([]))
    resolver.side_effect = UpstreamProxyRouteError("private route configuration")
    result = await send()
    assert result.outcome == "failed"
    assert result.http_status == 0
    assert result.error_code == "upstream_proxy_unavailable"
    assert "private route configuration" not in repr(result)
    post.assert_not_called()


async def test_transport_failure_never_returns_exception_text(completion_probe):
    _, _, configure, send = completion_probe
    post = configure(_Response([]))
    post.side_effect = CodexTransportError("raw secret proxy address")
    result = await send()
    assert result.http_status == 0
    assert result.outcome == "failed"
    assert result.error_code == "upstream_transport_error"
    assert "raw secret" not in repr(result)
