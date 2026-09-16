from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import socket
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


def load_launcher_module():
    path = Path(__file__).resolve().parents[2] / "clients" / "claude-lb-launch"
    loader = importlib.machinery.SourceFileLoader("claude_lb_launch_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def run_api_handler(launcher, request: bytes, fake_server: SimpleNamespace) -> bytes:
    client, server_socket = socket.socketpair()
    client.sendall(request)
    client.shutdown(socket.SHUT_WR)
    launcher._LbApiHandler(server_socket, ("127.0.0.1", 0), fake_server)
    server_socket.close()
    response = b""
    while chunk := client.recv(4096):
        response += chunk
    client.close()
    return response


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/health", True),
        ("/health/", True),
        ("/health?install=1", True),
        ("/v1/messages", True),
        ("/v1/messages/", True),
        ("/v1/messages?beta=1", True),
        ("/v1/messages/count_tokens", True),
        ("/v1/messages/count_tokens/?beta=1", True),
        ("/api/event_logging/v2/batch", False),
        ("/api/oauth/account/settings", False),
        ("/api/oauth/validate", False),
        ("/api/claude_cli/bootstrap", False),
        ("/api/claude_code_penguin_mode", False),
        ("/api/eval/sdk-test", False),
        ("/v1/code/sessions/session/worker/heartbeat", False),
        ("/v1/code/triggers", False),
        ("/v1/messages/batches", False),
        ("/v1/messages//", False),
        ("/v1/messages/count_tokens//", False),
        ("/health//", False),
        ("//evil.example/v1/messages", False),
        ("https://evil.example/v1/messages", False),
        ("/v1/files", False),
    ],
)
def test_anthropic_path_routing_defaults_unknown_auxiliary_paths_direct(path: str, expected: bool) -> None:
    launcher = load_launcher_module()

    assert launcher._routes_to_agent_lb(path) is expected


def test_ccgpt_build_command_locks_model_effort_and_bypass_perms() -> None:
    launcher = load_launcher_module()
    launcher.CCGPT_MODE = True

    command = launcher.build_command(["--model", "claude-opus-4-6", "--effort=max", "-p", "hello"])

    assert command == [
        "claude",
        "--model",
        "gpt-5.6-sol",
        "--effort",
        "high",
        "--permission-mode",
        "bypassPermissions",
        "-p",
        "hello",
    ]


def test_ccgpt_explicit_permission_mode_wins_over_bypass_default(monkeypatch) -> None:
    launcher = load_launcher_module()
    launcher.CCGPT_MODE = True
    monkeypatch.setenv("CC_PERMISSION_MODE", "acceptEdits")

    command = launcher.build_command(["--permission-mode", "plan", "-p", "hello"])

    assert command == [
        "claude",
        "--model",
        "gpt-5.6-sol",
        "--effort",
        "high",
        "--permission-mode",
        "plan",
        "-p",
        "hello",
    ]


def test_ccgpt_skip_permissions_flag_suppresses_bypass_injection() -> None:
    launcher = load_launcher_module()
    launcher.CCGPT_MODE = True

    command = launcher.build_command(["--dangerously-skip-permissions", "-p", "hello"])

    assert "--permission-mode" not in command


def test_ccgpt_proxy_rewrites_gpt_messages_and_token_count_but_rejects_claude() -> None:
    launcher = load_launcher_module()
    launcher.CCGPT_MODE = True

    gpt_body = b'{"model":"gpt-5.6-sol"}'
    claude_body = b'{"model":"claude-opus-4-8"}'
    assert launcher._ccgpt_upstream_path("/v1/messages", gpt_body) == "/v1/ccgpt/messages"
    with pytest.raises(launcher.CcgptModelViolation, match="rejected Messages request for claude-opus-4-8"):
        launcher._ccgpt_upstream_path("/v1/messages", claude_body)
    assert launcher._ccgpt_upstream_path("/v1/messages/count_tokens", claude_body) == "/v1/ccgpt/messages/count_tokens"
    assert launcher._ccgpt_upstream_path("/api/organizations", gpt_body) == "/api/organizations"
    assert launcher._ccgpt_upstream_path("/v1/messages/batches", gpt_body) == "/v1/messages/batches"
    assert launcher._ccgpt_upstream_path("/v1/messages//", gpt_body) == "/v1/messages//"
    assert (
        launcher._ccgpt_upstream_path("/v1/messages/count_tokens//", gpt_body)
        == "/v1/messages/count_tokens//"
    )
    assert launcher._ccgpt_upstream_path("//evil.example/v1/messages", gpt_body) == "//evil.example/v1/messages"
    assert launcher._ccgpt_upstream_path("/v1/messages/?beta=1", gpt_body) == "/v1/ccgpt/messages/?beta=1"
    assert (
        launcher._ccgpt_upstream_path("/v1/messages/count_tokens/?beta=1", gpt_body)
        == "/v1/ccgpt/messages/count_tokens/?beta=1"
    )


def test_ccgpt_http_shim_rejects_claude_before_upstream(monkeypatch) -> None:
    launcher = load_launcher_module()
    launcher.CCGPT_MODE = True
    monkeypatch.setattr(
        launcher.urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not contact upstream")),
    )
    client, server_socket = socket.socketpair()
    body = b'{"model":"claude-fable-5","messages":[]}'
    request = (
        b"POST /v1/messages HTTP/1.1\r\n"
        b"Host: api.anthropic.com\r\n"
        + f"Content-Length: {len(body)}\r\n".encode()
        + b"Content-Type: application/json\r\nConnection: close\r\n\r\n"
        + body
    )
    client.sendall(request)
    client.shutdown(socket.SHUT_WR)
    fake_server = SimpleNamespace(
        parent_pid=os.getpid(),
        shared=False,
        session_id="test-session",
        ccgpt_mode=True,
        upstream_base_url="http://127.0.0.1:9",
    )

    launcher._LbApiHandler(server_socket, ("127.0.0.1", 0), fake_server)
    server_socket.close()
    response = b""
    while chunk := client.recv(4096):
        response += chunk
    client.close()

    assert response.startswith(b"HTTP/1.1 400")
    assert b'"type": "invalid_request_error"' in response
    assert b"rejected Messages request for claude-fable-5" in response


def test_regular_cc_never_rewrites_messages_even_for_gpt_model() -> None:
    launcher = load_launcher_module()
    launcher.CCGPT_MODE = False

    assert launcher._ccgpt_upstream_path("/v1/messages", b'{"model":"gpt-5.6-sol"}') == "/v1/messages"
    assert (
        launcher._ccgpt_upstream_path("/v1/messages/count_tokens", b'{"model":"gpt-5.6-sol"}')
        == "/v1/messages/count_tokens"
    )


def test_shared_proxy_preserves_identity_headers_and_does_not_rewrite_models(monkeypatch) -> None:
    import io

    launcher = load_launcher_module()
    launcher.CCGPT_MODE = True
    requests = []

    def urlopen(request, timeout):
        requests.append(request)
        body = io.BytesIO(b"{}")
        return SimpleNamespace(
            status=200,
            headers={"content-type": "application/json"},
            read=body.read,
            close=lambda: None,
        )

    monkeypatch.setattr(launcher.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(
        launcher.http.client,
        "HTTPSConnection",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Messages must route to agent-lb")),
    )
    client, server_socket = socket.socketpair()
    body = b'{"model":"gpt-5.6-sol","messages":[]}'
    request = (
        b"POST /v1/messages HTTP/1.1\r\n"
        b"Host: api.anthropic.com\r\n"
        + f"Content-Length: {len(body)}\r\n".encode()
        + b"Content-Type: application/json\r\n"
        + b"X-Claude-Session-Id: desktop-session\r\n"
        + b"X-Identity-Token: keep-me\r\n"
        + b"Connection: close\r\n\r\n"
        + body
    )
    client.sendall(request)
    client.shutdown(socket.SHUT_WR)
    fake_server = SimpleNamespace(
        parent_pid=None,
        shared=True,
        session_id="",
        ccgpt_mode=False,
        upstream_base_url="http://127.0.0.1:2455",
    )

    launcher._LbApiHandler(server_socket, ("127.0.0.1", 0), fake_server)
    server_socket.close()
    while client.recv(4096):
        pass
    client.close()

    assert len(requests) == 1
    assert requests[0].full_url == "http://127.0.0.1:2455/v1/messages"
    headers = {key.lower(): value for key, value in requests[0].header_items()}
    assert headers["x-claude-session-id"] == "desktop-session"
    assert headers["x-identity-token"] == "keep-me"


def test_auxiliary_request_routes_direct_with_original_credentials_and_identity(monkeypatch) -> None:
    import io

    launcher = load_launcher_module()
    calls = []

    class FakeConnection:
        def __init__(self, host, port, *, timeout, context):
            calls.append(("connect", host, port, timeout, context))

        def request(self, method, path, *, body, headers):
            calls.append(("request", method, path, body, headers))

        def getresponse(self):
            body = io.BytesIO(b'{"accepted":true}')
            return SimpleNamespace(
                status=202,
                headers={"content-type": "application/json", "x-anthropic-request-id": "direct-1"},
                read=body.read,
            )

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(launcher.http.client, "HTTPSConnection", FakeConnection)
    monkeypatch.setattr(
        launcher.urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("auxiliary traffic must not reach agent-lb")),
    )
    body = b'{"events":[{"name":"test"}]}'
    request = (
        b"POST /api/event_logging/v2/batch?source=desktop HTTP/1.1\r\n"
        b"Host: api.anthropic.com\r\n"
        + f"Content-Length: {len(body)}\r\n".encode()
        + b"Content-Type: application/json\r\n"
        + b"Authorization: Bearer oauth-token\r\n"
        + b"X-Api-Key: direct-key\r\n"
        + b"Anthropic-Version: 2023-06-01\r\n"
        + b"Cookie: session=direct-cookie\r\n"
        + b"X-Claude-Session-Id: inbound-session\r\n"
        + b"Connection: close\r\n\r\n"
        + body
    )
    fake_server = SimpleNamespace(
        parent_pid=os.getpid(),
        shared=False,
        session_id="launcher-synthetic-session",
        ccgpt_mode=False,
        upstream_base_url="http://127.0.0.1:9",
    )

    response = run_api_handler(launcher, request, fake_server)

    assert response.startswith(b"HTTP/1.1 202")
    assert b"x-anthropic-request-id: direct-1" in response.lower()
    assert response.endswith(b'{"accepted":true}')
    assert calls[0][0:4] == ("connect", "api.anthropic.com", 443, 600)
    _, method, path, forwarded_body, headers = calls[1]
    assert (method, path, forwarded_body) == (
        "POST",
        "/api/event_logging/v2/batch?source=desktop",
        body,
    )
    normalized_headers = {key.lower(): value for key, value in headers.items()}
    assert normalized_headers["authorization"] == "Bearer oauth-token"
    assert normalized_headers["x-api-key"] == "direct-key"
    assert normalized_headers["anthropic-version"] == "2023-06-01"
    assert normalized_headers["cookie"] == "session=direct-cookie"
    assert normalized_headers["x-claude-session-id"] == "inbound-session"
    assert "host" not in normalized_headers
    assert "connection" not in normalized_headers
    assert "launcher-synthetic-session" not in normalized_headers.values()
    assert calls[-1] == ("close",)


@pytest.mark.parametrize("status", [307, 401, 429, 500])
def test_direct_auxiliary_http_status_passes_through_without_redirect_or_failover(monkeypatch, status: int) -> None:
    import io

    launcher = load_launcher_module()
    requests = []

    class FakeConnection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, method, path, *, body, headers):
            requests.append((method, path, body, headers))

        def getresponse(self):
            body = io.BytesIO(b"upstream-body")
            return SimpleNamespace(
                status=status,
                headers={"content-type": "text/plain", "location": "/next"},
                read=body.read,
            )

        def close(self):
            pass

    monkeypatch.setattr(launcher.http.client, "HTTPSConnection", FakeConnection)
    monkeypatch.setattr(
        launcher.urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not fail over to agent-lb")),
    )
    fake_server = SimpleNamespace(
        parent_pid=None,
        shared=True,
        session_id="",
        ccgpt_mode=False,
        upstream_base_url="http://127.0.0.1:9",
    )

    response = run_api_handler(
        launcher,
        b"GET /v1/code/triggers HTTP/1.1\r\nHost: api.anthropic.com\r\nConnection: close\r\n\r\n",
        fake_server,
    )

    assert response.startswith(f"HTTP/1.1 {status}".encode())
    assert b"location: /next" in response.lower()
    assert response.endswith(b"upstream-body")
    assert len(requests) == 1


def test_direct_auxiliary_connect_failure_is_one_shot_502_without_lb_retry(monkeypatch) -> None:
    launcher = load_launcher_module()
    attempts = []

    class FailingConnection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, method, path, *, body, headers):
            attempts.append(path)
            raise ConnectionRefusedError("direct Anthropic unavailable")

        def close(self):
            pass

    monkeypatch.setattr(launcher.http.client, "HTTPSConnection", FailingConnection)
    monkeypatch.setattr(
        launcher.urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not fail over to agent-lb")),
    )
    monkeypatch.setattr(
        launcher.time,
        "sleep",
        lambda *_: (_ for _ in ()).throw(AssertionError("direct failures must not enter LB retry backoff")),
    )
    fake_server = SimpleNamespace(
        parent_pid=None,
        shared=True,
        session_id="",
        ccgpt_mode=False,
        upstream_base_url="http://127.0.0.1:9",
    )

    response = run_api_handler(
        launcher,
        b"GET /v1/messages/batches HTTP/1.1\r\nHost: api.anthropic.com\r\nConnection: close\r\n\r\n",
        fake_server,
    )

    assert attempts == ["/v1/messages/batches"]
    assert response.startswith(b"HTTP/1.1 502")
    assert b'"code": "claude_lb_shim_error"' in response
    assert b"direct Anthropic unavailable" in response


def test_regular_cc_preserves_explicit_model_and_adds_configured_effort(monkeypatch) -> None:
    launcher = load_launcher_module()
    launcher.CCGPT_MODE = False
    monkeypatch.setenv("CC_EFFORT_LEVEL", "xhigh")
    monkeypatch.setenv("CC_PERMISSION_MODE", "plan")

    command = launcher.build_command(["--model", "claude-opus-4-8", "-p", "hello"])

    assert command == [
        "claude",
        "--effort",
        "xhigh",
        "--permission-mode",
        "plan",
        "--model",
        "claude-opus-4-8",
        "-p",
        "hello",
    ]


def test_regular_launcher_defaults_to_fable_5_1_1m_high(monkeypatch) -> None:
    launcher = load_launcher_module()
    launcher.CCGPT_MODE = False
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.delenv("CC_EFFORT_LEVEL", raising=False)

    command = launcher.build_command(["-p", "hello"])

    assert command == [
        "claude",
        "--model",
        "claude-fable-5-1[1m]",
        "--effort",
        "high",
        "-p",
        "hello",
    ]


def test_ccgpt_capability_probe_requires_native_token_count(monkeypatch) -> None:
    launcher = load_launcher_module()

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"input_tokens":12}'

    monkeypatch.setattr(launcher.urllib.request, "urlopen", lambda request, timeout: Response())
    monkeypatch.setattr(
        launcher,
        "lb_json",
        lambda path, **kwargs: {"accounts": [{"provider": "openai", "status": "active"}]},
    )

    assert launcher._probe_ccgpt_at("http://127.0.0.1:2455", timeout=1) == (True, "")


def test_ccgpt_capability_probe_rejects_endpoint_without_openai_pool(monkeypatch) -> None:
    launcher = load_launcher_module()

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"input_tokens":12}'

    monkeypatch.setattr(launcher.urllib.request, "urlopen", lambda request, timeout: Response())
    monkeypatch.setattr(launcher, "lb_json", lambda path, **kwargs: {"accounts": []})

    assert launcher._probe_ccgpt_at("http://127.0.0.1:2455", timeout=1) == (
        False,
        "no active OpenAI accounts",
    )


def test_ccgpt_endpoint_uses_capability_probe_without_health_probe(monkeypatch) -> None:
    launcher = load_launcher_module()
    monkeypatch.setattr(launcher, "_lb_candidates", lambda: [("local", "http://127.0.0.1:2455")])
    monkeypatch.setattr(
        launcher,
        "_probe_health_at",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("redundant health probe")),
    )
    monkeypatch.setattr(launcher, "_probe_ccgpt_at", lambda url, timeout: (True, ""))

    assert launcher.prepare_ccgpt_endpoint() is True


def test_remote_preference_retains_local_fallback(monkeypatch) -> None:
    launcher = load_launcher_module()
    monkeypatch.setenv("CLAUDE_LB_LOCAL_URL", "http://127.0.0.1:2455")
    monkeypatch.setenv("CLAUDE_LB_BASE_URL", "https://studio.example:2455")
    monkeypatch.setenv("CLAUDE_LB_PREFER_REMOTE", "1")
    monkeypatch.delenv("CLAUDE_LB_LOCAL_PREFER", raising=False)

    assert launcher._lb_candidates() == [
        ("remote", "https://studio.example:2455"),
        ("local", "http://127.0.0.1:2455"),
    ]


def test_normal_launcher_claims_without_health_probe(monkeypatch) -> None:
    launcher = load_launcher_module()
    monkeypatch.setattr(launcher, "_lb_candidates", lambda: [("local", "http://127.0.0.1:2455")])
    monkeypatch.setattr(
        launcher,
        "_probe_health_at",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("redundant health probe")),
    )
    monkeypatch.setattr(
        launcher,
        "_claim_at_endpoint",
        lambda url, session_id, model, quota_key, deadline, request_timeout=None: (
            {"accountId": "account-1", "alias": "Local"},
            "",
        ),
    )
    monkeypatch.setattr(launcher, "lb_json", lambda *args, **kwargs: {"accounts": []})

    assert launcher.print_lb_banner([], "session-1") is True


def test_interactive_launcher_uses_ready_probe_without_eager_claim(monkeypatch) -> None:
    launcher = load_launcher_module()
    monkeypatch.setattr(launcher, "_lb_candidates", lambda: [("local", "http://127.0.0.1:2455")])
    monkeypatch.setattr(
        launcher,
        "_probe_interactive_ready",
        lambda url, timeout, retries, gap: ("ready", ""),
    )
    monkeypatch.setattr(
        launcher,
        "_local_launchd_job_loaded",
        lambda: (_ for _ in ()).throw(AssertionError("healthy path must not inspect launchd")),
    )
    monkeypatch.setattr(
        launcher,
        "_claim_at_endpoint",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("interactive startup must not eagerly claim")),
    )

    assert launcher.prepare_interactive_endpoint() is True
    assert launcher.AGENT_LB_BASE_URL == "http://127.0.0.1:2455"


def test_ready_probe_targets_readiness_endpoint(monkeypatch) -> None:
    launcher = load_launcher_module()
    requested_paths: list[str] = []
    monkeypatch.setattr(
        launcher,
        "lb_json",
        lambda path, **kwargs: requested_paths.append(path) or {"status": "ready"},
    )

    assert launcher._probe_health_at("http://127.0.0.1:2455", retries=1, timeout=1.0, gap=0.0) == (
        True,
        "",
        False,
    )
    assert requested_paths == ["/health/ready"]


def test_interactive_launcher_waits_for_loaded_local_lb(monkeypatch, capsys) -> None:
    launcher = load_launcher_module()
    probes = iter(
        [
            (False, "connection refused", True),
            (True, "", False),
        ]
    )
    now = [0.0]
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("CLAUDE_LB_STARTUP_GRACE_SECONDS", "3")
    monkeypatch.setattr(launcher, "_lb_candidates", lambda: [("local", "http://127.0.0.1:2455")])
    monkeypatch.setattr(
        launcher,
        "_probe_interactive_ready",
        lambda *args, **kwargs: ("unreachable", "connection refused"),
    )
    monkeypatch.setattr(launcher, "_probe_health_at", lambda *args, **kwargs: next(probes))
    monkeypatch.setattr(launcher, "_local_launchd_job_loaded", lambda: True)
    monkeypatch.setattr(launcher.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(launcher.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))

    assert launcher.prepare_interactive_endpoint() is True

    lines = capsys.readouterr().err.splitlines()
    assert lines.count("cc: agent-lb is starting — waiting up to 3s for /health/ready ...") == 1
    assert lines.count("cc: agent-lb is ready — continuing with local LB") == 1
    assert launcher.AGENT_LB_BASE_URL == "http://127.0.0.1:2455"


def test_interactive_launcher_zero_grace_skips_launchd_check(monkeypatch) -> None:
    launcher = load_launcher_module()
    monkeypatch.setenv("CLAUDE_LB_STARTUP_GRACE_SECONDS", "0")
    monkeypatch.setattr(launcher, "_lb_candidates", lambda: [("local", "http://127.0.0.1:2455")])
    monkeypatch.setattr(
        launcher,
        "_probe_interactive_ready",
        lambda *args, **kwargs: ("unreachable", "connection refused"),
    )
    monkeypatch.setattr(
        launcher,
        "_local_launchd_job_loaded",
        lambda: (_ for _ in ()).throw(AssertionError("zero grace must not inspect launchd")),
    )

    assert launcher.prepare_interactive_endpoint() is False


def test_interactive_launcher_never_applies_startup_grace_to_remote(monkeypatch) -> None:
    launcher = load_launcher_module()
    monkeypatch.setattr(launcher, "_lb_candidates", lambda: [("remote", "https://studio.example:2455")])
    monkeypatch.setattr(
        launcher,
        "_probe_interactive_ready",
        lambda *args, **kwargs: ("unreachable", "network unreachable"),
    )
    monkeypatch.setattr(
        launcher,
        "_wait_for_local_startup",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("remote must not receive local grace")),
    )

    assert launcher.prepare_interactive_endpoint() is False


class _BlackHoleServer:
    """Accepts TCP connections and never answers: an LB whose event loop is stalled."""

    def __enter__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.url = f"http://127.0.0.1:{self.sock.getsockname()[1]}"
        return self

    def __exit__(self, *exc):
        self.sock.close()


def _refused_url() -> str:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return f"http://127.0.0.1:{port}"


def test_interactive_probe_reports_busy_on_timeout_without_retrying() -> None:
    launcher = load_launcher_module()
    with _BlackHoleServer() as lb:
        started = time.monotonic()
        state, error = launcher._probe_interactive_ready(lb.url, timeout=0.2, retries=3, gap=1.0)
        elapsed = time.monotonic() - started
    assert state == "busy"
    assert error
    assert elapsed < 1.0, f"a timed-out probe must not be retried (took {elapsed:.2f}s)"


def test_interactive_probe_reports_unreachable_instantly() -> None:
    launcher = load_launcher_module()
    started = time.monotonic()
    state, _ = launcher._probe_interactive_ready(_refused_url(), timeout=1.0, retries=3, gap=1.0)
    elapsed = time.monotonic() - started
    assert state == "unreachable"
    assert elapsed < 0.5, f"refused must not sleep between retries (took {elapsed:.2f}s)"


def test_interactive_probe_retries_error_responses(monkeypatch) -> None:
    launcher = load_launcher_module()
    import io
    import urllib.error

    attempts: list[int] = []
    sleeps: list[float] = []

    def not_ready(path, **kwargs):
        attempts.append(1)
        raise urllib.error.HTTPError(path, 503, "not ready", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr(launcher, "lb_json", not_ready)
    monkeypatch.setattr(launcher.time, "sleep", sleeps.append)

    assert launcher._probe_interactive_ready("http://127.0.0.1:2455", timeout=1.0, retries=3, gap=0.5)[0] == "down"
    assert len(attempts) == 3
    assert sleeps == [0.5, 0.5]


def test_interactive_launcher_routes_through_busy_lb(monkeypatch, capsys) -> None:
    launcher = load_launcher_module()
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("CLAUDE_LB_STRICT_READY", raising=False)
    monkeypatch.setattr(launcher, "_lb_candidates", lambda: [("local", "http://127.0.0.1:2455")])
    monkeypatch.setattr(launcher, "_probe_interactive_ready", lambda *args, **kwargs: ("busy", "timed out"))
    monkeypatch.setattr(
        launcher,
        "_wait_for_local_startup",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("busy is not unreachable")),
    )

    assert launcher.prepare_interactive_endpoint() is True
    assert launcher.AGENT_LB_BASE_URL == "http://127.0.0.1:2455"
    assert "LB busy, routing anyway" in capsys.readouterr().err


def test_interactive_launcher_strict_mode_treats_busy_as_down(monkeypatch) -> None:
    launcher = load_launcher_module()
    monkeypatch.setenv("CLAUDE_LB_STRICT_READY", "1")
    monkeypatch.setattr(launcher, "_lb_candidates", lambda: [("local", "http://127.0.0.1:2455")])
    monkeypatch.setattr(launcher, "_probe_interactive_ready", lambda *args, **kwargs: ("busy", "timed out"))

    assert launcher.prepare_interactive_endpoint() is False


def test_headless_claim_defers_on_timeout_instead_of_failing() -> None:
    launcher = load_launcher_module()
    with _BlackHoleServer() as lb:
        started = time.monotonic()
        claim, error, retry_at = launcher.claim_session_route("s", "claude-fable-5-1", "k", lb.url, timeout=0.1)
        elapsed = time.monotonic() - started
    assert claim is launcher.DEFERRED_CLAIM
    assert (error, retry_at) == ("", None)
    # one retry with a doubled budget, then defer: ~0.1s + ~0.2s
    assert 0.25 <= elapsed < 1.0, elapsed


def test_headless_claim_still_fails_when_unreachable() -> None:
    launcher = load_launcher_module()
    claim, error, retry_at = launcher.claim_session_route("s", "claude-fable-5-1", "k", _refused_url(), timeout=0.5)
    assert claim is None
    assert error
    assert retry_at is None


def test_headless_banner_routes_through_deferred_claim(monkeypatch, capsys) -> None:
    launcher = load_launcher_module()
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(launcher, "_lb_candidates", lambda: [("local", "http://127.0.0.1:2455")])
    monkeypatch.setattr(
        launcher,
        "_claim_at_endpoint",
        lambda url, session_id, model, quota_key, deadline, request_timeout=None: (launcher.DEFERRED_CLAIM, ""),
    )
    monkeypatch.setattr(
        launcher,
        "lb_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("deferred claim must skip banner enrichment")),
    )

    assert launcher.print_lb_banner([], "session-1") is True
    assert launcher.AGENT_LB_BASE_URL == "http://127.0.0.1:2455"
    assert "LB busy, sticky account selected on first message" in capsys.readouterr().err


def test_prune_stale_shim_files_only_touches_old_launcher_files(tmp_path) -> None:
    launcher = load_launcher_module()
    now = 1_700_000_000.0
    old = now - launcher.SHIM_READY_FILE_MAX_AGE_SECONDS - 60
    fresh = now - 60
    for name, mtime in (
        ("cc-1-1.proxy", old),
        ("cc-2-2.proxy", fresh),
        ("desktop.proxy", old),
        ("other.proxy", old),
        ("cc-3-3.proxy.tmp", old),
    ):
        path = tmp_path / name
        path.write_text("1\n")
        os.utime(path, (mtime, mtime))

    assert launcher._prune_stale_shim_files(tmp_path, now=now) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "cc-2-2.proxy",
        "cc-3-3.proxy.tmp",
        "desktop.proxy",
        "other.proxy",
    ]
    assert launcher._prune_stale_shim_files(tmp_path / "missing", now=now) == 0


def test_local_launchd_check_uses_configured_label_and_timeout(monkeypatch) -> None:
    from types import SimpleNamespace

    launcher = load_launcher_module()
    calls: list[tuple[list[str], dict]] = []
    monkeypatch.setenv("CLAUDE_LB_LAUNCHD_LABEL", "com.example.agent-lb")
    monkeypatch.setattr(launcher.os, "getuid", lambda: 501)
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)) or SimpleNamespace(returncode=0),
    )

    assert launcher._local_launchd_job_loaded() is True
    assert calls[0][0] == ["launchctl", "print", "gui/501/com.example.agent-lb"]
    assert calls[0][1]["timeout"] == 1.0


def test_local_launchd_check_treats_timeout_as_not_loaded(monkeypatch) -> None:
    launcher = load_launcher_module()
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            launcher.subprocess.TimeoutExpired(cmd="launchctl print", timeout=1.0)
        ),
    )

    assert launcher._local_launchd_job_loaded() is False


def test_health_unreachable_classifier_excludes_http_and_read_timeouts() -> None:
    import socket
    import urllib.error

    launcher = load_launcher_module()

    assert launcher._is_unreachable_health_error(urllib.error.URLError(ConnectionRefusedError())) is True
    assert (
        launcher._is_unreachable_health_error(
            urllib.error.HTTPError("http://lb", 503, "Service Unavailable", {}, None)
        )
        is False
    )
    assert launcher._is_unreachable_health_error(urllib.error.URLError(socket.timeout())) is False


def test_proxy_spawn_uses_portable_subprocess(monkeypatch, tmp_path) -> None:
    launcher = load_launcher_module()
    calls: list[list[str]] = []
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda command, **kwargs: calls.append(command))

    launcher._spawn_lb_proxy(tmp_path / "ready", "session", 456, "http://127.0.0.1:2455")

    assert calls and calls[0][1].endswith("claude-lb-launch")


def test_desktop_proxy_cli_uses_default_fixed_port(monkeypatch, tmp_path) -> None:
    launcher = load_launcher_module()
    calls = []
    monkeypatch.delenv("CLAUDE_LB_DESKTOP_PROXY_PORT", raising=False)
    monkeypatch.setattr(launcher.sys, "argv", ["claude-lb-launch", "--desktop-proxy"])
    monkeypatch.setattr(launcher, "proxy_ready_path", lambda session_id: tmp_path / f"{session_id}.proxy")
    monkeypatch.setattr(launcher, "run_lb_proxy", lambda *args, **kwargs: calls.append((args, kwargs)))

    launcher.main()

    assert calls == [
        (
            (str(tmp_path / "desktop.proxy"), "", None, launcher.AGENT_LB_BASE_URL),
            {"listen_port": 2458, "shared": True},
        )
    ]


def test_desktop_proxy_cli_accepts_explicit_fixed_port_and_upstream(monkeypatch, tmp_path) -> None:
    launcher = load_launcher_module()
    calls = []
    monkeypatch.setattr(
        launcher.sys,
        "argv",
        ["claude-lb-launch", "--desktop-proxy", "3456", "http://127.0.0.1:9999/"],
    )
    monkeypatch.setattr(launcher, "proxy_ready_path", lambda session_id: tmp_path / f"{session_id}.proxy")
    monkeypatch.setattr(launcher, "run_lb_proxy", lambda *args, **kwargs: calls.append((args, kwargs)))

    launcher.main()

    assert calls == [
        (
            (str(tmp_path / "desktop.proxy"), "", None, "http://127.0.0.1:9999"),
            {"listen_port": 3456, "shared": True},
        )
    ]


def test_shared_proxy_has_no_parent_watchdog_or_ccgpt_rewrite(monkeypatch, tmp_path) -> None:
    launcher = load_launcher_module()
    launcher.CCGPT_MODE = True
    servers = []

    class FakeTlsContext:
        def __init__(self, protocol):
            pass

        def load_cert_chain(self, **kwargs):
            pass

    class FakeServer:
        server_address = ("127.0.0.1", 2458)

        def __init__(self, address, handler):
            servers.append(self)

        def serve_forever(self):
            return

    monkeypatch.setattr(launcher, "ensure_mitm_certs", lambda: None)
    monkeypatch.setattr(launcher.ssl, "SSLContext", FakeTlsContext)
    monkeypatch.setattr(launcher, "_ThreadingProxyServer", FakeServer)
    monkeypatch.setattr(
        launcher.threading,
        "Thread",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("shared proxy must not start a parent watchdog")),
    )

    launcher.run_lb_proxy(
        str(tmp_path / "desktop.proxy"),
        "",
        None,
        "http://127.0.0.1:2455",
        listen_port=2458,
        shared=True,
    )

    assert len(servers) == 1
    assert servers[0].shared is True
    assert servers[0].parent_pid is None
    assert servers[0].ccgpt_mode is False


@pytest.mark.parametrize("disabled", [True, False])
def test_plain_claude_paths_bypass_shared_proxy_without_clobbering_exclusions(monkeypatch, disabled) -> None:
    launcher = load_launcher_module()
    launcher.CCGPT_MODE = False
    monkeypatch.setenv("NO_PROXY", "localhost,.internal")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    monkeypatch.setenv("CLAUDE_LB_DRY_RUN", "1")
    if disabled:
        monkeypatch.setenv("CLAUDE_LB_DISABLE", "1")
    else:
        monkeypatch.delenv("CLAUDE_LB_DISABLE", raising=False)
        monkeypatch.setattr(launcher, "prepare_interactive_endpoint", lambda: False)
    monkeypatch.setattr(launcher.sys, "argv", ["claude-lb-launch"])

    launcher.main()

    assert launcher.os.environ["NO_PROXY"] == "localhost,.internal,api.anthropic.com"
    assert launcher.os.environ["no_proxy"] == "127.0.0.1,api.anthropic.com"


def test_format_lb_pick_error_prettifies_anthropic_selection_failure() -> None:
    launcher = load_launcher_module()
    body = (
        '{"error":{"message":"3 Anthropic accounts exist, but none are selectable for '
        "claude-fable-5/anthropic_top_thinking; statuses: active=1, rate_limited=2. "
        'OpenAI accounts are not eligible for Claude routing.",'
        '"type":"server_error","code":"no_available_anthropic_accounts"}}'
    )

    assert launcher.format_lb_pick_error(body) == (
        "Claude accounts are all at limit or unavailable "
        "(3 accounts; active=1, rate_limited=2). OpenAI accounts cannot serve Claude."
    )


def test_format_lb_pick_error_preserves_anthropic_quota_detail() -> None:
    launcher = load_launcher_module()
    body = (
        '{"error":{"message":"2 Anthropic accounts exist, but none are selectable for '
        "claude-fable-5/anthropic_top; statuses: active=1, quota_exceeded=1. "
        "Model quota: anthropic_top cooldown excluded 1 account until 2026-06-12T09:20:24; "
        "1 account remained after the anthropic_top prefilter; "
        "selector reason: no_available_anthropic_accounts. "
        'OpenAI accounts are not eligible for Claude routing. Limits reset at 2026-06-12T09:20:24.",'
        '"type":"server_error","code":"no_available_anthropic_accounts"}}'
    )

    assert launcher.format_lb_pick_error(body) == (
        "Claude accounts are all at limit or unavailable (2 accounts; active=1, quota_exceeded=1). "
        "Model quota: anthropic_top cooldown excluded 1 account until 2026-06-12T09:20:24; "
        "1 account remained after the anthropic_top prefilter; "
        "selector reason: no_available_anthropic_accounts. OpenAI accounts cannot serve Claude."
    )


def test_format_lb_pick_error_handles_all_rate_limited_accounts() -> None:
    launcher = load_launcher_module()
    body = (
        '{"error":{"message":"3 Anthropic accounts exist, but none are selectable for '
        "claude-fable-5/anthropic_top_thinking; statuses: rate_limited=3. "
        'OpenAI accounts are not eligible for Claude routing.",'
        '"type":"server_error","code":"no_available_anthropic_accounts"}}'
    )

    assert launcher.format_lb_pick_error(body) == (
        "all Claude accounts are rate-limited (3 accounts). OpenAI accounts cannot serve Claude."
    )


def test_format_lb_pick_error_preserves_plain_text_body() -> None:
    launcher = load_launcher_module()

    assert launcher.format_lb_pick_error("upstream unavailable") == "upstream unavailable"


def test_retry_at_from_error_body_prefers_retry_at_iso() -> None:
    launcher = load_launcher_module()
    body = (
        '{"error":{"message":"cooling down","code":"anthropic_quota_cooldown",'
        '"retryAt":"2026-06-10T18:20:01Z","retryAfterSeconds":900}}'
    )

    assert launcher.retry_at_from_error_body(body) == 1781115601


def test_retry_at_from_error_body_falls_back_to_retry_after_seconds() -> None:
    launcher = load_launcher_module()
    body = '{"error":{"message":"cooling down","retryAfterSeconds":120}}'

    retry_at = launcher.retry_at_from_error_body(body)
    assert retry_at is not None
    assert abs(retry_at - (time.time() + 120)) < 5


def test_retry_at_from_error_body_returns_none_without_metadata() -> None:
    launcher = load_launcher_module()

    assert launcher.retry_at_from_error_body('{"error":{"message":"nope"}}') is None
    assert launcher.retry_at_from_error_body("not json") is None


def test_account_pressure_includes_fable_available_state() -> None:
    launcher = load_launcher_module()

    account = {
        "alias": "Claude A",
        "fableEligible": True,
        "usage": {"primaryRemainingPercent": 100, "secondaryRemainingPercent": 35},
        "additionalQuotas": [
            {
                "quotaKey": "anthropic_fable_scoped_weekly",
                "primaryWindow": {"usedPercent": 84, "resetAt": None, "windowMinutes": 10080},
            },
            {
                "quotaKey": "anthropic_top_thinking",
                "primaryWindow": {"usedPercent": 0, "resetAt": 0},
            },
        ],
    }

    *_, reason = launcher.account_pressure(account, "anthropic_top_thinking")

    assert reason == "left: top-thinking 100% · 5h 100% · weekly 35% · fable 16% left"


def test_account_pressure_includes_fable_out_state() -> None:
    launcher = load_launcher_module()

    account = {
        "alias": "Claude B",
        "fableEligible": False,
        "usage": {"primaryRemainingPercent": 100, "secondaryRemainingPercent": 18},
        "additionalQuotas": [
            {
                "quotaKey": "anthropic_fable_scoped_weekly",
                "primaryWindow": {"usedPercent": 100, "resetAt": None, "windowMinutes": 10080},
            },
            {
                "quotaKey": "anthropic_top_thinking",
                "primaryWindow": {"usedPercent": 0, "resetAt": 0},
            },
        ],
    }

    *_, reason = launcher.account_pressure(account, "anthropic_top_thinking")

    assert reason == "left: top-thinking 100% · 5h 100% · weekly 18% · fable out (0% left)"


def test_account_pressure_falls_back_to_fable_availability_without_scoped_usage() -> None:
    launcher = load_launcher_module()

    account = {
        "alias": "Claude B",
        "fableEligible": False,
        "usage": {"primaryRemainingPercent": 100, "secondaryRemainingPercent": 18},
        "additionalQuotas": [
            {
                "quotaKey": "anthropic_top_thinking",
                "primaryWindow": {"usedPercent": 0, "resetAt": 0},
            }
        ],
    }

    *_, reason = launcher.account_pressure(account, "anthropic_top_thinking")

    assert reason == "left: top-thinking 100% · 5h 100% · weekly 18% · fable out"


def test_account_pressure_omits_unknown_fable_state() -> None:
    launcher = load_launcher_module()

    account = {
        "alias": "Claude C",
        "usage": {"primaryRemainingPercent": 100, "secondaryRemainingPercent": 41},
        "additionalQuotas": [
            {
                "quotaKey": "anthropic_top_thinking",
                "primaryWindow": {"usedPercent": 0, "resetAt": 0},
            }
        ],
    }

    *_, reason = launcher.account_pressure(account, "anthropic_top_thinking")

    assert reason == "left: top-thinking 100% · 5h 100% · weekly 41%"


def test_should_wait_for_reset_honors_mode_and_deadline(monkeypatch) -> None:
    launcher = load_launcher_module()
    now = time.time()

    monkeypatch.delenv("CLAUDE_LB_WAIT_FOR_LIMIT", raising=False)
    assert launcher.should_wait_for_reset(int(now + 60), now + 3600) is True
    assert launcher.should_wait_for_reset(int(now + 7200), now + 3600) is False
    assert launcher.should_wait_for_reset(None, now + 3600) is False

    monkeypatch.setenv("CLAUDE_LB_WAIT_FOR_LIMIT", "never")
    assert launcher.should_wait_for_reset(int(now + 60), now + 3600) is False


def test_build_resume_command_preserves_model_and_output_format(monkeypatch) -> None:
    launcher = load_launcher_module()
    monkeypatch.delenv("CC_EFFORT_LEVEL", raising=False)
    monkeypatch.delenv("CC_PERMISSION_MODE", raising=False)

    command = launcher.build_resume_command(
        ["-p", "do the thing", "--model", "claude-fable-5", "--output-format", "json"],
        "11111111-2222-3333-4444-555555555555",
    )

    assert command[:1] == ["claude"]
    assert command[command.index("--model") + 1] == "claude-fable-5"
    assert command[command.index("--output-format") + 1] == "json"
    assert command[command.index("--resume") + 1] == "11111111-2222-3333-4444-555555555555"
    assert "-p" in command
    assert "do the thing" not in command


def test_headless_invocation_detects_print_flags() -> None:
    launcher = load_launcher_module()

    assert launcher.headless_invocation(["-p", "hi"]) is True
    assert launcher.headless_invocation(["--print", "hi"]) is True
    assert launcher.headless_invocation(["chat"]) is False


def test_shim_connect_error_is_retryable_only_for_connection_failures() -> None:
    import socket
    import urllib.error

    launcher = load_launcher_module()
    classify = launcher._is_retryable_shim_connect_error

    # Connection refused/reset (LB restarting) — safe to retry, request never landed.
    assert classify(ConnectionRefusedError()) is True
    assert classify(ConnectionResetError()) is True
    assert classify(urllib.error.URLError(ConnectionRefusedError())) is True

    # A real HTTP response — never retried; its status passes straight through.
    assert classify(urllib.error.HTTPError("http://lb", 503, "Service Unavailable", {}, None)) is False

    # Read timeout — request may be in flight, so do not re-send (no double-process).
    assert classify(urllib.error.URLError(socket.timeout())) is False
    assert classify(RuntimeError("boom")) is False


def test_shim_connect_retry_budget_outlasts_watchdog_recovery() -> None:
    launcher = load_launcher_module()

    budget = sum(
        min(launcher.SHIM_CONNECT_BACKOFF_DEFAULT * (2**attempt), launcher.SHIM_CONNECT_BACKOFF_CAP_DEFAULT)
        for attempt in range(launcher.SHIM_CONNECT_RETRIES_DEFAULT)
    )

    # Watchdog revival of an unloaded LB takes up to ~65s (2 x 30s ticks +
    # startup); the shim must keep retrying well past that instead of
    # surfacing a 502 broken pipe to the agent.
    assert budget >= 100


def test_tunnel_bucket_from_env_defaults_and_disable(monkeypatch) -> None:
    launcher = load_launcher_module()

    monkeypatch.delenv("CLAUDE_LB_TUNNEL_RATE_MBPS", raising=False)
    bucket = launcher._tunnel_bucket_from_env()
    assert bucket is not None
    assert bucket.rate == pytest.approx(launcher.SHIM_TUNNEL_RATE_MBPS_DEFAULT * 125_000.0)

    monkeypatch.setenv("CLAUDE_LB_TUNNEL_RATE_MBPS", "0")
    assert launcher._tunnel_bucket_from_env() is None

    # NaN passes a plain <=0 gate; it must disable shaping, not poison sleeps.
    monkeypatch.setenv("CLAUDE_LB_TUNNEL_RATE_MBPS", "nan")
    assert launcher._tunnel_bucket_from_env() is None

    monkeypatch.setenv("CLAUDE_LB_TUNNEL_RATE_MBPS", "8")
    bucket = launcher._tunnel_bucket_from_env()
    assert bucket is not None
    assert bucket.rate == pytest.approx(1_000_000.0)


def test_tunnel_bucket_paces_aggregate_relay_rate() -> None:
    launcher = load_launcher_module()

    # 1 MB/s with a 10KB burst: four 50KB chunks book 0.2s of schedule; after
    # the burst window the caller must have slept ~0.14s of it.
    bucket = launcher._TunnelBucket(1_000_000.0, burst_bytes=10_000.0)
    started = time.monotonic()
    for _ in range(4):
        bucket.throttle(50_000)
    elapsed = time.monotonic() - started
    assert elapsed >= 0.12

    # Disabled shaping never sleeps regardless of volume.
    unlimited = launcher._TunnelBucket(0.0)
    started = time.monotonic()
    unlimited.throttle(10**9)
    assert time.monotonic() - started < 0.05


def test_tunnel_bucket_small_writes_bypass_sleep_but_debit_schedule() -> None:
    launcher = load_launcher_module()

    bucket = launcher._TunnelBucket(1_000_000.0, burst_bytes=10_000.0)
    # Small frames (RC websocket scale) pass without sleeping...
    started = time.monotonic()
    for _ in range(5):
        bucket.throttle(1_000)
    assert time.monotonic() - started < 0.05
    # ...but they booked schedule: once the booked backlog exceeds the burst
    # window, small writes throttle too, so chunking small cannot dodge
    # shaping. 30 x 8KB = 240KB at 1 MB/s must take ~0.24s minus the burst.
    started = time.monotonic()
    for _ in range(30):
        bucket.throttle(8_000)
    assert time.monotonic() - started >= 0.15


def test_splice_relays_both_directions_and_reports_totals() -> None:
    launcher = load_launcher_module()

    client_far, client_near = socket.socketpair()
    upstream_near, upstream_far = socket.socketpair()
    result: dict[str, tuple[int, int]] = {}

    def run() -> None:
        result["totals"] = launcher._splice(client_near, upstream_near)

    import threading

    pump = threading.Thread(target=run, daemon=True)
    pump.start()

    # Stay under the AF_UNIX socketpair buffer: a payload larger than the
    # in-flight capacity deadlocks this single-threaded send/read sequence.
    client_far.sendall(b"a" * 4_000)
    received = b""
    while len(received) < 4_000:
        received += upstream_far.recv(65536)
    upstream_far.sendall(b"b" * 1_000)
    reply = client_far.recv(65536)

    client_far.close()
    pump.join(timeout=5)
    upstream_far.close()

    assert not pump.is_alive()
    assert received == b"a" * 4_000
    assert reply == b"b" * 1_000
    assert result["totals"] == (4_000, 1_000)
