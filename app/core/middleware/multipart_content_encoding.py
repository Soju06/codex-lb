from __future__ import annotations

import logging

from fastapi import FastAPI
from starlette._utils import get_route_path
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.audit.service import AuditService
from app.core.errors import dashboard_error, openai_error
from app.core.runtime_logging import log_error_response

_LIMITED_MULTIPART_PATHS = frozenset(
    {
        "/api/accounts/import",
        "/api/accounts/bundle/import/preflight",
        "/api/accounts/bundle/import/commit",
        "/backend-api/transcribe",
        "/v1/audio/transcriptions",
        "/v1/images/edits",
    }
)
_OPENAI_MULTIPART_PATH_PREFIXES = ("/backend-api", "/v1")
_ACCOUNT_BUNDLE_PATH_PREFIX = "/api/accounts/bundle/"
_ACCOUNT_BUNDLE_OPERATIONS = {
    "/api/accounts/bundle/export": "export",
    "/api/accounts/bundle/import/preflight": "preflight",
    "/api/accounts/bundle/import/commit": "commit",
}
_ACCOUNT_BUNDLE_NO_STORE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, private",
    "Pragma": "no-cache",
    "Expires": "0",
}
_ACCOUNT_BUNDLE_AUDIT_CONTEXT_STATE = "_codex_lb_account_bundle_audit_context"
_CONTENT_ENCODING_GATE_HANDLED_STATE = "_codex_lb_multipart_content_encoding_gate_handled"
_UNSUPPORTED_CONTENT_ENCODING_STATE = "_codex_lb_unsupported_multipart_content_encoding"

logger = logging.getLogger(__name__)


def _canonical_route_path(scope: Scope) -> str:
    path = get_route_path(scope)
    if path != "/":
        path = path.rstrip("/")
    return path


class UnsupportedMultipartContentEncoding(Exception):
    pass


def is_route_owned_multipart_operation(scope: Scope) -> bool:
    return scope.get("method") == "POST" and _canonical_route_path(scope) in _LIMITED_MULTIPART_PATHS


def account_bundle_no_store_headers(scope: Scope) -> dict[str, str]:
    if _canonical_route_path(scope).startswith(_ACCOUNT_BUNDLE_PATH_PREFIX):
        return dict(_ACCOUNT_BUNDLE_NO_STORE_HEADERS)
    return {}


def audit_account_bundle_request_failure(scope: Scope, *, status_code: int) -> None:
    if scope["type"] != "http" or scope.get("method") != "POST":
        return

    operation = _ACCOUNT_BUNDLE_OPERATIONS.get(_canonical_route_path(scope))
    if operation is None:
        return

    state = scope.setdefault("state", {})
    audit_context = state.get(_ACCOUNT_BUNDLE_AUDIT_CONTEXT_STATE)
    if not isinstance(audit_context, dict):
        audit_context = {"failure_audited": False}
        state[_ACCOUNT_BUNDLE_AUDIT_CONTEXT_STATE] = audit_context
    if audit_context.get("failure_audited") is True:
        return

    audit_context["failure_audited"] = True
    client = scope.get("client")
    AuditService.log_async(
        "account_bundle_request_failed",
        actor_ip=client[0] if client else None,
        details={"operation": operation, "outcome": f"http_{status_code}"},
    )


def _uses_openai_multipart_errors(scope: Scope) -> bool:
    path = _canonical_route_path(scope)
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in _OPENAI_MULTIPART_PATH_PREFIXES)


def multipart_error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    param: str | None = None,
) -> JSONResponse:
    uses_openai_errors = _uses_openai_multipart_errors(request.scope)
    response_code = "invalid_request_error" if uses_openai_errors and code == "invalid_request" else code
    log_error_response(
        logger,
        request,
        status_code,
        response_code,
        message,
        category="openai_error_response" if uses_openai_errors else "dashboard_error_response",
    )
    if uses_openai_errors:
        error = openai_error(response_code, message, error_type="invalid_request_error")
        if param is not None:
            error["error"]["param"] = param
        return JSONResponse(status_code=status_code, content=error)
    return JSONResponse(status_code=status_code, content=dashboard_error(response_code, message))


def _without_content_encoding(scope: Scope, *, unsupported: bool = False) -> Scope:
    copied = dict(scope)
    copied["headers"] = [
        (name, value) for name, value in scope.get("headers", []) if name.lower() != b"content-encoding"
    ]
    state = dict(scope.get("state", {}))
    state[_CONTENT_ENCODING_GATE_HANDLED_STATE] = True
    if unsupported:
        state[_UNSUPPORTED_CONTENT_ENCODING_STATE] = True
    copied["state"] = state
    return copied


def multipart_content_encoding_gate_was_applied(scope: Scope) -> bool:
    return scope.get("state", {}).get(_CONTENT_ENCODING_GATE_HANDLED_STATE) is True


def raise_for_unsupported_multipart_content_encoding(request: Request) -> None:
    if getattr(request.state, _UNSUPPORTED_CONTENT_ENCODING_STATE, False) is True:
        raise UnsupportedMultipartContentEncoding


def mark_account_bundle_failure_audited(request: Request) -> None:
    context = getattr(request.state, _ACCOUNT_BUNDLE_AUDIT_CONTEXT_STATE, None)
    if isinstance(context, dict):
        context["failure_audited"] = True


class MultipartContentEncodingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        no_store_headers = account_bundle_no_store_headers(scope)
        if no_store_headers:
            state = scope.setdefault("state", {})
            state.setdefault(
                _ACCOUNT_BUNDLE_AUDIT_CONTEXT_STATE,
                {"failure_audited": False},
            )
            original_send = send

            async def send(message):
                if message["type"] == "http.response.start":
                    headers = MutableHeaders(scope=message)
                    for name, value in no_store_headers.items():
                        headers[name] = value
                    status = int(message["status"])
                    if status >= 400:
                        audit_account_bundle_request_failure(scope, status_code=status)
                await original_send(message)

        headers = Headers(scope=scope)
        if not is_route_owned_multipart_operation(scope):
            await self.app(scope, receive, send)
            return

        content_encoding_values = headers.getlist("content-encoding")
        if not content_encoding_values:
            await self.app(scope, receive, send)
            return
        encodings = [
            encoding.strip().lower()
            for value in content_encoding_values
            for encoding in value.split(",")
            if encoding.strip()
        ]
        if not encodings or all(value == "identity" for value in encodings):
            await self.app(_without_content_encoding(scope), receive, send)
            return
        await self.app(_without_content_encoding(scope, unsupported=True), receive, send)


def add_multipart_content_encoding_middleware(app: FastAPI) -> None:
    app.add_middleware(MultipartContentEncodingMiddleware)
