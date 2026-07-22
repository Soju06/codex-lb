from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import AsyncByteStream
from starlette.datastructures import UploadFile

import app.modules.accounts.api as accounts_api_module
from app.core.auth.dependencies import require_dashboard_write_access
from app.core.exceptions import DashboardPermissionError
from app.modules.accounts.schemas import AccountImportResponse

pytestmark = pytest.mark.integration

_MIB = 1024 * 1024


class _NeverReadStream(AsyncByteStream):
    def __init__(self) -> None:
        self.iterated = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.iterated = True
        raise AssertionError("request body must not be consumed")
        yield b""  # pragma: no cover


def _import_response() -> AccountImportResponse:
    return AccountImportResponse(
        account_id="acc_bounded_import",
        email="bounded-import@example.com",
        plan_type="plus",
        status="active",
    )


@pytest.mark.asyncio
async def test_account_import_auth_failure_does_not_read_multipart_body(
    app_instance,
    async_client,
) -> None:
    async def reject_write_access() -> None:
        raise DashboardPermissionError(
            "Read-only dashboard access cannot modify dashboard state",
            code="read_only_access",
        )

    stream = _NeverReadStream()
    app_instance.dependency_overrides[require_dashboard_write_access] = reject_write_access
    try:
        response = await async_client.post(
            "/api/accounts/import",
            content=stream,
            headers={"content-type": "multipart/form-data; boundary=never-read"},
        )
    finally:
        app_instance.dependency_overrides.pop(require_dashboard_write_access, None)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "read_only_access"
    assert stream.iterated is False


@pytest.mark.asyncio
async def test_account_import_rejects_compressed_body_after_auth_without_reading(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_import(*_args: object, **_kwargs: object) -> AccountImportResponse:
        raise AssertionError("compressed upload must not reach account import")

    monkeypatch.setattr("app.modules.accounts.service.AccountsService.import_account", fail_import)
    stream = _NeverReadStream()

    response = await async_client.post(
        "/api/accounts/import",
        content=stream,
        headers={
            "content-type": "multipart/form-data; boundary=never-read",
            "content-encoding": "gzip",
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert stream.iterated is False


@pytest.mark.asyncio
async def test_account_import_allows_exact_file_limit_and_closes_spool_before_service(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed_uploads: list[UploadFile] = []
    original_close = UploadFile.close

    async def record_close(upload: UploadFile) -> None:
        await original_close(upload)
        closed_uploads.append(upload)

    async def import_account(_service: object, raw: bytes) -> AccountImportResponse:
        assert len(raw) == _MIB
        assert len(closed_uploads) == 1
        assert closed_uploads[0].file.closed
        return _import_response()

    audit_events: list[str] = []
    monkeypatch.setattr(UploadFile, "close", record_close)
    monkeypatch.setattr("app.modules.accounts.service.AccountsService.import_account", import_account)
    monkeypatch.setattr(
        accounts_api_module.AuditService,
        "log_async",
        lambda event, **_kwargs: audit_events.append(event),
    )

    response = await async_client.post(
        "/api/accounts/import",
        files={"auth_json": ("auth.json", b"x" * _MIB, "application/json")},
    )

    assert response.status_code == 200
    assert response.json()["accountId"] == "acc_bounded_import"
    assert audit_events == ["account_created"]


@pytest.mark.asyncio
async def test_account_import_file_limit_rejects_without_service_or_audit_side_effects(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_calls = 0
    audit_calls = 0

    async def import_account(*_args: object, **_kwargs: object) -> AccountImportResponse:
        nonlocal service_calls
        service_calls += 1
        return _import_response()

    def record_audit(*_args: object, **_kwargs: object) -> None:
        nonlocal audit_calls
        audit_calls += 1

    monkeypatch.setattr("app.modules.accounts.service.AccountsService.import_account", import_account)
    monkeypatch.setattr(accounts_api_module.AuditService, "log_async", record_audit)

    response = await async_client.post(
        "/api/accounts/import",
        files={"auth_json": ("auth.json", b"x" * (_MIB + 1), "application/json")},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert service_calls == 0
    assert audit_calls == 0


@pytest.mark.asyncio
async def test_account_import_declared_body_limit_rejects_without_service_work(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_calls = 0

    async def import_account(*_args: object, **_kwargs: object) -> AccountImportResponse:
        nonlocal service_calls
        service_calls += 1
        return _import_response()

    monkeypatch.setattr("app.modules.accounts.service.AccountsService.import_account", import_account)

    response = await async_client.post(
        "/api/accounts/import",
        files={"auth_json": ("auth.json", b"{}", "application/json")},
        headers={"content-length": str(2 * _MIB + 1)},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert service_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_parts",
    [
        {
            "data": {"unexpected": "value"},
            "files": {"auth_json": ("auth.json", b"{}", "application/json")},
        },
        {
            "files": [
                ("auth_json", ("first.json", b"{}", "application/json")),
                ("auth_json", ("second.json", b"{}", "application/json")),
            ],
        },
    ],
    ids=["additional-text-part", "duplicate-file-part"],
)
async def test_account_import_rejects_additional_parts_before_service(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
    request_parts: dict[str, object],
) -> None:
    async def fail_import(*_args: object, **_kwargs: object) -> AccountImportResponse:
        raise AssertionError("invalid multipart shape must not reach account import")

    monkeypatch.setattr("app.modules.accounts.service.AccountsService.import_account", fail_import)

    response = await async_client.post("/api/accounts/import", **request_parts)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "http_400"


@pytest.mark.asyncio
async def test_account_import_missing_file_retains_dashboard_validation_envelope(async_client) -> None:
    response = await async_client.post(
        "/api/accounts/import",
        content=b"--empty--\r\n",
        headers={"content-type": "multipart/form-data; boundary=empty"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_account_import_missing_content_type_retains_dashboard_validation_envelope(async_client) -> None:
    response = await async_client.post("/api/accounts/import", content=b"not multipart")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_account_import_text_auth_json_retains_dashboard_validation_envelope(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_import(*_args: object, **_kwargs: object) -> AccountImportResponse:
        raise AssertionError("text auth_json must not reach account import")

    monkeypatch.setattr("app.modules.accounts.service.AccountsService.import_account", fail_import)

    response = await async_client.post(
        "/api/accounts/import",
        files={"auth_json": (None, "{}", "application/json")},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
