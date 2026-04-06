from __future__ import annotations

from starlette.requests import Request

from app.core.request_locality import is_local_request


def _request(*, client_host: str, host: str) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"host", host.encode("utf-8"))],
        "client": (client_host, 50000),
        "server": (host.split(":", 1)[0], 80),
        "scheme": "http",
        "query_string": b"",
    }
    return Request(scope)


def test_loopback_with_local_host_is_local() -> None:
    request = _request(client_host="127.0.0.1", host="localhost")
    assert is_local_request(request) is True


def test_loopback_with_non_local_host_is_not_local() -> None:
    request = _request(client_host="127.0.0.1", host="lb.example")
    assert is_local_request(request) is False
