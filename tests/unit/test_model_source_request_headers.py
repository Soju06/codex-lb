"""Source-direction request headers are constructed, never forwarded (#2123 WP-C2; preflight finding v).

An overflow dispatch to a non-ChatGPT source must never carry ChatGPT-internal
telemetry (``x-openai-subagent``, ``x-openai-memgen-request``, ``x-codex-*``,
``session-id``, ``thread-id``, ``x-client-request-id``, ``x-oai-attestation``,
``chatgpt-account-id``, ``originator`` ...). The guarantee is structural rather
than a filter: ``forwarding._source_headers`` builds ``Accept``,
``Content-Type`` and the source's own ``Authorization`` from scratch, every
``aiohttp`` call in the module passes exactly that builder's result, and the
module has no access to the inbound request at all. Direct source routing is
therefore unchanged by construction; the route-level captures live in
``tests/integration/test_model_source_routing.py`` (direct) and
``tests/integration/test_subscription_overflow_routing.py`` (overflow).
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.modules.model_sources import forwarding

# The complete set of header names the proxy itself puts on a source request.
SOURCE_REQUEST_HEADER_NAMES = frozenset({"Accept", "Content-Type", "Authorization"})

_FORWARDING_PATH = Path(forwarding.__file__)
# Attribute calls that issue an HTTP request on an ``aiohttp`` session.
_HTTP_METHODS = frozenset({"post", "get", "put", "patch", "delete", "request"})


def _source(*, with_key: bool) -> Any:
    return SimpleNamespace(
        id="src_headers",
        base_url="http://127.0.0.1:9/v1",
        api_key_encrypted="ciphertext" if with_key else None,
    )


def _encryptor() -> Any:
    return SimpleNamespace(decrypt=lambda ciphertext: "source-secret")


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("accept", [None, "*/*"])
@pytest.mark.parametrize("content_type", ["application/json", None])
@pytest.mark.parametrize("with_key", [True, False])
def test_source_headers_are_exactly_accept_content_type_and_the_source_authorization(
    stream: bool, accept: str | None, content_type: str | None, with_key: bool
) -> None:
    headers = forwarding._source_headers(
        _source(with_key=with_key),
        encryptor=_encryptor(),
        stream=stream,
        accept=accept,
        content_type=content_type,
    )

    expected = {"Accept"}
    if content_type is not None:
        expected.add("Content-Type")
    if with_key:
        expected.add("Authorization")
    assert set(headers) == expected
    assert set(headers) <= SOURCE_REQUEST_HEADER_NAMES
    assert headers["Accept"] == (accept or ("text/event-stream" if stream else "application/json"))
    if content_type is not None:
        assert headers["Content-Type"] == content_type
    if with_key:
        assert headers["Authorization"] == "Bearer source-secret"


def _module() -> ast.Module:
    return ast.parse(_FORWARDING_PATH.read_text(encoding="utf-8"))


def test_every_source_request_passes_the_constructed_headers_only() -> None:
    module = _module()
    http_calls = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _HTTP_METHODS
    ]
    header_kwargs = [keyword for call in http_calls for keyword in call.keywords if keyword.arg == "headers"]
    assert header_kwargs, "forwarding.py issues no source request with headers"
    for keyword in header_kwargs:
        value = keyword.value
        is_builder_call = (
            isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "_source_headers"
        )
        assert is_builder_call, (keyword.lineno, ast.dump(value))
    # No request is issued with a positional/unknown header mapping either.
    for call in http_calls:
        assert not any(isinstance(arg, ast.Starred) for arg in call.args), call.lineno
        assert all(keyword.arg is not None for keyword in call.keywords), call.lineno


def test_forwarding_has_no_access_to_inbound_request_headers() -> None:
    module = _module()
    source = _FORWARDING_PATH.read_text(encoding="utf-8")
    assert "request.headers" not in source
    assert "websocket.headers" not in source
    imported_modules: set[str] = set()
    for node in ast.walk(module):
        if isinstance(node, ast.ImportFrom):
            imported_modules.add(node.module or "")
        elif isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
    assert not any(name.startswith(("fastapi", "starlette")) for name in imported_modules if name), sorted(
        imported_modules
    )
    header_like_parameters = [
        (function.name, argument.arg)
        for function in ast.walk(module)
        if isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef)
        for argument in [*function.args.args, *function.args.kwonlyargs, *function.args.posonlyargs]
        if "headers" in argument.arg.lower()
    ]
    assert header_like_parameters == [], header_like_parameters


def test_source_header_builder_signature_has_no_inbound_input() -> None:
    module = _module()
    builders = [
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == "_source_headers"
    ]
    assert len(builders) == 1
    (builder,) = builders
    parameters = [argument.arg for argument in [*builder.args.args, *builder.args.kwonlyargs]]
    assert parameters == ["source", "encryptor", "stream", "accept", "content_type"], parameters


# -- shared with the route-level captures ---------------------------------------------------------------

# ChatGPT-internal telemetry a native Codex request carries (responses_metadata.rs, client.rs); none of it may
# reach an OpenAI-compatible source, on the overflow path or on direct routing.
CODEX_TELEMETRY_REQUEST_HEADERS: dict[str, str] = {
    "user-agent": "codex_cli_rs/0.153.4 (Linux 6.8.0; x86_64) header-proof",
    "originator": "codex_cli_rs",
    "x-openai-subagent": "review",
    "x-openai-memgen-request": "true",
    "x-codex-installation-id": "inst_header_proof",
    "x-codex-window-id": "win_header_proof",
    "x-codex-turn-metadata": '{"turn_id":"turn_header_proof"}',
    "x-codex-parent-thread-id": "thr_parent_header_proof",
    "session-id": "sess_header_proof",
    "thread-id": "thr_header_proof",
    "x-client-request-id": "creq_header_proof",
    "x-oai-attestation": "attestation-header-proof",
    "chatgpt-account-id": "acct_header_proof",
}

# Exactly what a source receives on the wire: the constructed trio plus the transport headers ``aiohttp`` adds
# itself (``Host``, ``Content-Length``, its own ``User-Agent`` and ``Accept-Encoding``). Nothing else.
SOURCE_WIRE_HEADER_NAMES: frozenset[str] = frozenset(
    {"host", "accept", "accept-encoding", "content-type", "content-length", "authorization", "user-agent"}
)


def assert_source_saw_only_constructed_headers(received: dict[str, str], *, source_token: str) -> None:
    """Route-level oracle: the stub upstream recorded ``dict(request.headers)`` for one source request."""

    lowered = {name.lower(): value for name, value in received.items()}
    assert set(lowered) == SOURCE_WIRE_HEADER_NAMES, sorted(lowered)
    assert lowered["authorization"] == f"Bearer {source_token}", "the source's own credential, never the client's"
    assert lowered["user-agent"].startswith("Python/"), lowered["user-agent"]
    assert lowered["accept"] == "text/event-stream"
    assert lowered["content-type"] == "application/json"
    leaked = {name for name in lowered if name in CODEX_TELEMETRY_REQUEST_HEADERS and name != "user-agent"}
    assert leaked == set(), sorted(leaked)
