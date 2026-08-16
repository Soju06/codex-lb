from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.auth.dashboard_access import guest_principal
from app.core.auth.dependencies import validate_dashboard_session
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, ApiKey, ModelSource, RequestLog
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.request_logs.repository import RequestLogsRepository

pytestmark = pytest.mark.integration


def _make_account(account_id: str, email: str) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=email,
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


@pytest.mark.asyncio
async def test_request_logs_api_returns_recent(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_logs", "logs@example.com"))
        session.add(
            ApiKey(
                id="key_logs_1",
                name="Debug Key",
                key_hash="hash_logs_1",
                key_prefix="sk-test",
            )
        )
        await session.commit()

        now = utcnow()
        await logs_repo.add_log(
            account_id="acc_logs",
            request_id="req_logs_1",
            model="gpt-5.1",
            input_tokens=100,
            output_tokens=200,
            latency_ms=1200,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=1),
            transport="http",
        )
        await logs_repo.add_log(
            account_id="acc_logs",
            request_id="req_logs_2",
            archive_request_id="archive_req_logs_2",
            model="legacy-model",
            input_tokens=50,
            output_tokens=0,
            latency_ms=300,
            status="error",
            error_code="rate_limit_exceeded",
            error_message="Rate limit reached",
            failure_phase="owner_forward_status",
            failure_detail="owner_forward_non_200",
            failure_exception_type="ProxyResponseError",
            upstream_status_code=503,
            upstream_error_code="bridge_owner_forward_failed",
            bridge_stage="owner_forward",
            requested_at=now,
            api_key_id="key_logs_1",
            transport="websocket",
            connection_request_kind="prewarm",
        )

    response = await async_client.get("/api/request-logs?limit=2")
    assert response.status_code == 200
    body = response.json()
    payload = body["requests"]
    assert len(payload) == 2
    assert body["total"] == 2
    assert body["hasMore"] is False

    latest = payload[0]
    assert latest["status"] == "rate_limit"
    assert latest["apiKeyId"] == "key_logs_1"
    assert latest["apiKeyName"] == "Debug Key"
    assert latest["errorCode"] == "rate_limit_exceeded"
    assert latest["requestId"] == "req_logs_2"
    assert latest["archiveRequestId"] == "archive_req_logs_2"
    assert latest["errorMessage"] == "Rate limit reached"
    assert latest["failurePhase"] == "owner_forward_status"
    assert latest["failureDetail"] == "owner_forward_non_200"
    assert latest["failureExceptionType"] == "ProxyResponseError"
    assert latest["upstreamStatusCode"] == 503
    assert latest["upstreamErrorCode"] == "bridge_owner_forward_failed"
    assert latest["bridgeStage"] == "owner_forward"
    assert latest["costBreakdown"] == {
        "inputUsd": None,
        "cachedInputUsd": None,
        "outputUsd": None,
        "totalUsd": None,
    }
    assert latest["transport"] == "websocket"
    assert latest["requestKind"] == "normal"
    assert latest["connectionRequestKind"] == "prewarm"

    older = payload[1]
    assert older["status"] == "ok"
    assert older["requestId"] == "req_logs_1"
    assert older["archiveRequestId"] == "req_logs_1"
    assert older["apiKeyId"] is None
    assert older["apiKeyName"] is None
    assert older["tokens"] == 300
    assert older["inputTokens"] == 100
    assert older["outputTokens"] == 200
    assert older["cachedInputTokens"] is None
    assert older["costBreakdown"] == {
        "inputUsd": None,
        "cachedInputUsd": None,
        "outputUsd": pytest.approx(0.002),
        "totalUsd": pytest.approx(0.002125),
    }
    assert older["transport"] == "http"
    assert older["requestKind"] == "normal"
    assert older["connectionRequestKind"] is None


@pytest.mark.asyncio
async def test_request_logs_api_returns_upstream_proxy_route_metadata(async_client, db_setup):
    del db_setup
    async with SessionLocal() as session:
        logs_repo = RequestLogsRepository(session)
        now = utcnow()
        await logs_repo.add_log(
            account_id=None,
            request_id="req_route_success",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=20,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now - timedelta(seconds=1),
            upstream_proxy_route_mode="account_bound",
            upstream_proxy_pool_id="pool_route",
            upstream_proxy_endpoint_id="endpoint_route",
            upstream_proxy_fallback_used=True,
        )
        await logs_repo.add_log(
            account_id=None,
            request_id="req_route_fail_closed",
            model="gpt-5.1",
            input_tokens=None,
            output_tokens=None,
            latency_ms=0,
            status="error",
            error_code="upstream_proxy_unavailable",
            requested_at=now,
            upstream_proxy_route_mode="account_bound",
            upstream_proxy_pool_id="pool_route",
            upstream_proxy_fail_closed_reason="no_healthy_endpoint",
        )

    response = await async_client.get("/api/request-logs?limit=2")
    assert response.status_code == 200
    fail_closed, success = response.json()["requests"]
    assert fail_closed["upstreamProxyRouteMode"] == "account_bound"
    assert fail_closed["upstreamProxyPoolId"] == "pool_route"
    assert fail_closed["upstreamProxyEndpointId"] is None
    assert fail_closed["upstreamProxyFallbackUsed"] is None
    assert fail_closed["upstreamProxyFailClosedReason"] == "no_healthy_endpoint"
    assert success["upstreamProxyRouteMode"] == "account_bound"
    assert success["upstreamProxyPoolId"] == "pool_route"
    assert success["upstreamProxyEndpointId"] == "endpoint_route"
    assert success["upstreamProxyFallbackUsed"] is True
    assert success["upstreamProxyFailClosedReason"] is None


@pytest.mark.asyncio
async def test_request_logs_api_returns_model_source_metadata(async_client, db_setup):
    del db_setup
    async with SessionLocal() as session:
        logs_repo = RequestLogsRepository(session)
        await logs_repo.add_log(
            account_id=None,
            model_source_id="source_history",
            model_source_kind="openai_compatible",
            request_id="req_source_history",
            model="source-model",
            input_tokens=10,
            output_tokens=20,
            latency_ms=100,
            status="success",
            error_code=None,
            source="model_source",
        )

    response = await async_client.get("/api/request-logs?limit=1")
    assert response.status_code == 200
    latest = response.json()["requests"][0]
    assert latest["requestId"] == "req_source_history"
    assert latest["source"] == "model_source"
    assert latest["modelSourceId"] == "source_history"
    assert latest["modelSourceKind"] == "openai_compatible"


@pytest.mark.asyncio
async def test_request_log_model_source_id_survives_source_delete(db_setup):
    del db_setup
    async with SessionLocal() as session:
        session.add(
            ModelSource(
                id="source_deleted_history",
                name="deleted history",
                base_url="https://deleted-history.example.invalid/v1",
            )
        )
        await session.commit()
        logs_repo = RequestLogsRepository(session)
        saved = await logs_repo.add_log(
            account_id=None,
            model_source_id="source_deleted_history",
            model_source_kind="openai_compatible",
            request_id="req_deleted_source_history",
            model="source-model",
            input_tokens=10,
            output_tokens=20,
            latency_ms=100,
            status="success",
            error_code=None,
            source="model_source",
        )
        source = await session.get(ModelSource, "source_deleted_history")
        assert source is not None
        await session.delete(source)
        await session.commit()

        persisted = await session.get(RequestLog, saved.id)
        assert persisted is not None
        assert persisted.model_source_id == "source_deleted_history"
        assert persisted.model_source_kind == "openai_compatible"


@pytest.mark.asyncio
async def test_request_logs_api_returns_useragent_fields(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_logs_useragent", "ua-logs@example.com"))

        now = utcnow()
        await logs_repo.add_log(
            account_id="acc_logs_useragent",
            request_id="req_logs_useragent_present",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=20,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now,
            useragent="opencode/1.15.13 ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14",
            useragent_group="opencode",
            client_ip="203.0.113.7",
        )
        await logs_repo.add_log(
            account_id="acc_logs_useragent",
            request_id="req_logs_useragent_absent",
            model="gpt-5.1-mini",
            input_tokens=5,
            output_tokens=15,
            latency_ms=50,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=1),
        )

    response = await async_client.get("/api/request-logs?limit=2")
    assert response.status_code == 200
    payload = response.json()["requests"]
    assert [entry["requestId"] for entry in payload] == [
        "req_logs_useragent_present",
        "req_logs_useragent_absent",
    ]

    latest = payload[0]
    assert latest["useragent"] == "opencode/1.15.13 ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14"
    assert latest["useragentGroup"] == "opencode"
    assert latest["clientIp"] == "203.0.113.7"

    older = payload[1]
    assert older["useragent"] is None
    assert older["useragentGroup"] is None
    assert older["clientIp"] is None


@pytest.mark.asyncio
async def test_request_logs_api_redacts_sensitive_metadata_for_guest_and_preserves_admin(
    app_instance,
    async_client,
    db_setup,
):
    del db_setup
    async with SessionLocal() as session:
        logs_repo = RequestLogsRepository(session)
        await logs_repo.add_log(
            account_id=None,
            request_id="req_guest_visible",
            archive_request_id="archive_guest_hidden",
            conversation_id="conversation_guest_hidden",
            model="gpt-5.1",
            input_tokens=100,
            output_tokens=20,
            latency_ms=250,
            status="success",
            error_code=None,
            useragent="codex-cli/1.2.3 runtime/node",
            useragent_group="codex-cli",
            client_ip="203.0.113.17",
        )

    app_instance.dependency_overrides[validate_dashboard_session] = guest_principal
    try:
        guest_response = await async_client.get("/api/request-logs?limit=1")
    finally:
        app_instance.dependency_overrides.pop(validate_dashboard_session, None)

    assert guest_response.status_code == 200
    guest_entry = guest_response.json()["requests"][0]
    assert guest_entry["requestId"] == "req_guest_visible"
    assert guest_entry["archiveRequestId"] is None
    assert guest_entry["conversationId"] is None
    assert guest_entry["useragent"] is None
    assert guest_entry["clientIp"] is None
    assert guest_entry["useragentGroup"] == "codex-cli"
    assert guest_entry["model"] == "gpt-5.1"
    assert guest_entry["status"] == "ok"
    assert guest_entry["tokens"] == 120
    assert guest_entry["latencyMs"] == 250

    admin_response = await async_client.get("/api/request-logs?limit=1")
    assert admin_response.status_code == 200
    admin_entry = admin_response.json()["requests"][0]
    assert admin_entry["archiveRequestId"] == "archive_guest_hidden"
    assert admin_entry["conversationId"] == "conversation_guest_hidden"
    assert admin_entry["useragent"] == "codex-cli/1.2.3 runtime/node"
    assert admin_entry["clientIp"] == "203.0.113.17"


@pytest.mark.asyncio
async def test_request_logs_api_excludes_client_ip_from_guest_search_and_preserves_admin_search(
    app_instance,
    async_client,
    db_setup,
    monkeypatch,
):
    from app.modules.request_logs import repository as logs_repository_module

    del db_setup
    monkeypatch.setattr(logs_repository_module, "_COUNT_CACHE_TTL_SECONDS", 30.0)
    logs_repository_module._clear_recent_count_cache()
    async with SessionLocal() as session:
        logs_repo = RequestLogsRepository(session)
        await logs_repo.add_log(
            account_id=None,
            request_id="req_ip_search_target",
            model="gpt-5.1",
            input_tokens=100,
            output_tokens=20,
            latency_ms=250,
            status="success",
            error_code=None,
            client_ip="203.0.113.17",
        )

    try:
        app_instance.dependency_overrides[validate_dashboard_session] = guest_principal
        try:
            guest_response = await async_client.get("/api/request-logs?search=203.0.113")
        finally:
            app_instance.dependency_overrides.pop(validate_dashboard_session, None)

        assert guest_response.status_code == 200
        assert guest_response.json()["requests"] == []
        assert guest_response.json()["total"] == 0

        admin_response = await async_client.get("/api/request-logs?search=203.0.113")
        assert admin_response.status_code == 200
        assert [entry["requestId"] for entry in admin_response.json()["requests"]] == [
            "req_ip_search_target",
        ]
        assert admin_response.json()["total"] == 1
    finally:
        logs_repository_module._clear_recent_count_cache()


@pytest.mark.asyncio
async def test_request_logs_api_rejects_guest_conversation_filter_and_preserves_admin_aggregates(
    app_instance,
    async_client,
    db_setup,
):
    del db_setup
    async with SessionLocal() as session:
        logs_repo = RequestLogsRepository(session)
        await logs_repo.add_log(
            account_id=None,
            request_id="req_conversation_filter_target",
            conversation_id="conversation-filter-target",
            model="gpt-5.1",
            input_tokens=100,
            output_tokens=20,
            latency_ms=250,
            status="success",
            error_code=None,
            cost_usd=4.25,
        )

    app_instance.dependency_overrides[validate_dashboard_session] = guest_principal
    try:
        guest_response = await async_client.get(
            "/api/request-logs",
            params={"conversation_id": "conversation-filter-target"},
        )
    finally:
        app_instance.dependency_overrides.pop(validate_dashboard_session, None)

    assert guest_response.status_code == 403
    assert guest_response.json()["error"]["code"] == "admin_access_required"

    admin_response = await async_client.get(
        "/api/request-logs",
        params={"conversation_id": "conversation-filter-target"},
    )
    assert admin_response.status_code == 200
    admin_payload = admin_response.json()
    assert [entry["requestId"] for entry in admin_payload["requests"]] == [
        "req_conversation_filter_target",
    ]
    assert admin_payload["total"] == 1
    assert admin_payload["conversation"] == {
        "requestCount": 1,
        "aggregatedCostUsd": 4.25,
    }


@pytest.mark.asyncio
async def test_request_logs_api_lists_limit_warmup_rows(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_warmup_logs", "warmup-logs@example.com"))

        await logs_repo.add_log(
            account_id="acc_warmup_logs",
            request_id="req_normal_traffic",
            model="gpt-5.2",
            input_tokens=100,
            output_tokens=100,
            latency_ms=100,
            status="success",
            error_code=None,
            plan_type="plus",
        )
        await logs_repo.add_log(
            account_id="acc_warmup_logs",
            request_id="req_limit_warmup",
            model="gpt-5.1-codex-mini",
            input_tokens=1,
            output_tokens=1,
            latency_ms=10,
            status="success",
            error_code=None,
            plan_type="plus",
            request_kind="warmup",
        )

    response = await async_client.get("/api/request-logs?limit=10")
    assert response.status_code == 200
    body = response.json()
    request_ids = [entry["requestId"] for entry in body["requests"]]
    assert request_ids == ["req_limit_warmup", "req_normal_traffic"]
    assert body["requests"][0]["requestKind"] == "warmup"
    assert body["requests"][1]["requestKind"] == "normal"
    assert body["total"] == 2

    options_response = await async_client.get("/api/request-logs/options")
    assert options_response.status_code == 200
    option_models = [entry["model"] for entry in options_response.json()["modelOptions"]]
    assert "gpt-5.1-codex-mini" in option_models


@pytest.mark.asyncio
async def test_request_log_total_count_is_cached_per_filter_signature(async_client, db_setup, monkeypatch):
    """With the TTL enabled, repeated listings reuse the cached COUNT(*) for
    the same filter signature; distinct signatures count separately."""
    from sqlalchemy import event

    from app.db.session import engine
    from app.modules.request_logs import repository as logs_repository_module

    monkeypatch.setattr(logs_repository_module, "_COUNT_CACHE_TTL_SECONDS", 30.0)
    logs_repository_module._clear_recent_count_cache()

    count_statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT COUNT") and "request_logs" in statement:
            count_statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _capture)
    try:
        first = await async_client.get("/api/request-logs?limit=5")
        second = await async_client.get("/api/request-logs?limit=5&offset=5")
        filtered = await async_client.get("/api/request-logs?limit=5&status=ok")
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _capture)
        logs_repository_module._clear_recent_count_cache()

    assert first.status_code == 200
    assert second.status_code == 200
    assert filtered.status_code == 200
    assert first.json()["total"] == second.json()["total"]
    # One COUNT for the shared default signature (page 2 reuses it), one for
    # the status-filtered signature.
    assert len(count_statements) == 2
