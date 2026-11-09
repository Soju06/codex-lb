from __future__ import annotations

import json

import pytest
from sqlalchemy import select

import app.modules.proxy.api as proxy_api
import app.modules.proxy.service as proxy_service
from app.core.auth import generate_unique_account_id
from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.core.openai.models import CompactResponsePayload
from app.db.models import Account, ApiKey, ApiKeyUsageReservation
from app.db.session import SessionLocal
from app.modules.proxy.capability_lineage import CapabilityLineageAlias
from app.modules.proxy.capability_lineage_repository import CapabilityLineageRepository
from tests.integration.compact_test_helpers import _make_auth_json

pytestmark = pytest.mark.integration

ROUTES = [
    "/backend-api/codex/responses",
    "/backend-api/codex/v1/responses",
    "/v1/responses",
    "/backend-api/codex/responses/compact",
    "/v1/responses/compact",
]


async def _setup(client, *, scoped_to_ordinary=False, enabled=True, allow_second_blue=False, with_limit=False):
    ids = {}
    for name in ("ordinary", "blue-in", "blue-out"):
        email = name + "@example.com"
        result = await client.post(
            "/api/accounts/import",
            files={"auth_json": ("auth.json", json.dumps(_make_auth_json(name, email)), "application/json")},
        )
        assert result.status_code == 200
        ids[name] = generate_unique_account_id(name, email)
    async with SessionLocal() as session:
        for account in (await session.scalars(select(Account))).all():
            account.security_work_authorized = account.id != ids["ordinary"]
            if account.id == ids["ordinary"]:
                account.routing_policy = "burn_first"
        await session.commit()
    settings = await client.put("/api/settings", json={"apiKeyAuthEnabled": True, "stickyThreadsEnabled": False})
    assert settings.status_code == 200
    assigned = [ids["ordinary"]] if scoped_to_ordinary else [ids["ordinary"], ids["blue-in"]]
    if allow_second_blue:
        assigned.append(ids["blue-out"])
    result = await client.post(
        "/api/api-keys/",
        json={
            "name": "one-key",
            "automaticDaybreakRouting": enabled,
            "assignedAccountIds": assigned,
            **(
                {"limits": [{"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 100000}]}
                if with_limit
                else {}
            ),
        },
    )
    assert result.status_code == 200
    return {"Authorization": "Bearer " + result.json()["key"]}


def _install_upstream(
    monkeypatch, *, reject=False, forbid_sources=True, reject_first_account=False, unique_response_ids=False
):
    calls = []

    async def keep_fresh(self, account, *args, **kwargs):
        return account

    def record_attempt(payload, account_id):
        calls.append((account_id, payload.to_payload()))
        if reject_first_account and account_id == calls[0][0]:
            raise ProxyResponseError(429, openai_error("rate_limit_exceeded", "Test account quota exhausted"))
        if reject:
            raise ProxyResponseError(403, openai_error("model_not_found", "The model is unavailable for this account"))

    async def stream(payload, headers, access_token, account_id, **kwargs):
        record_attempt(payload, account_id)
        for kind in ("response.created", "response.completed"):
            event = {
                "type": kind,
                "response": {
                    "id": f"resp_native_http_{len(calls)}" if unique_response_ids else "resp_native_http",
                    "object": "response",
                    "status": "completed" if kind.endswith("completed") else "in_progress",
                    "output": [],
                },
            }
            yield "data: " + json.dumps(event) + "\n\n"

    async def compact(payload, headers, access_token, account_id, **kwargs):
        record_attempt(payload, account_id)
        return CompactResponsePayload.model_validate(
            {
                "id": f"resp_native_compact_{len(calls)}" if unique_response_ids else "resp_native_compact",
                "object": "response.compaction",
                "output": [{"type": "compaction", "encrypted_content": "opaque-test-content"}],
            }
        )

    async def forbid_source(*args, **kwargs):
        pytest.fail("Daybreak must not dispatch to an external model source")

    monkeypatch.setattr(proxy_service.ProxyService, "_ensure_fresh_with_budget", keep_fresh)
    monkeypatch.setattr(proxy_service, "core_stream_responses", stream)
    monkeypatch.setattr(proxy_service, "core_compact_responses", compact)
    if forbid_sources:
        monkeypatch.setattr(proxy_api, "_select_responses_model_source_with_continuity", forbid_source)
    return calls


def _payload(identifier):
    payload = {"model": "gpt-5.1", "instructions": "", "input": "test", "stream": True}
    if identifier == "legacy":
        payload["model"] = "gpt-daybreak-blue-latest"
    elif identifier == "native":
        payload["access_programs"] = {"cyber": "daybreak_blue"}
    return payload


@pytest.mark.parametrize("path", ROUTES + [path + "/" for path in ROUTES])
@pytest.mark.parametrize("identifier", ["native", "legacy"])
async def test_http_daybreak_uses_eligible_account_within_key_scope(async_client, monkeypatch, path, identifier):
    headers = await _setup(async_client)
    calls = _install_upstream(monkeypatch)
    payload = _payload(identifier)
    response = await async_client.post(path, json=payload, headers=headers, follow_redirects=True)
    assert response.status_code == 200, response.text
    assert [account for account, _ in calls] == ["blue-in"]
    assert calls[0][1]["model"] == payload["model"]
    if identifier == "native":
        assert calls[0][1]["access_programs"] == payload["access_programs"]


@pytest.mark.parametrize("path", ROUTES)
async def test_http_daybreak_empty_eligible_scope_never_uses_ordinary_or_outside_account(
    async_client, monkeypatch, path
):
    headers = await _setup(async_client, scoped_to_ordinary=True)
    calls = _install_upstream(monkeypatch)
    response = await async_client.post(path, json=_payload("native"), headers=headers)
    assert response.status_code >= 400 or "response.failed" in response.text, response.text
    assert calls == []
    assert "no_security_work_authorized_accounts" in response.text


@pytest.mark.parametrize("path", [ROUTES[0], ROUTES[3]])
async def test_http_daybreak_model_rejection_never_retries_ordinary_or_outside_account(async_client, monkeypatch, path):
    headers = await _setup(async_client)
    calls = _install_upstream(monkeypatch, reject=True)
    response = await async_client.post(path, json=_payload("native"), headers=headers)
    assert response.status_code >= 400 or "response.failed" in response.text
    assert [account for account, _ in calls] == ["blue-in"]


@pytest.mark.parametrize("path", [ROUTES[0], ROUTES[3]])
async def test_http_daybreak_previous_response_keeps_constraint(async_client, monkeypatch, path):
    headers = await _setup(async_client)
    calls = _install_upstream(monkeypatch)
    response = await async_client.post(ROUTES[0], json=_payload("native"), headers=headers)
    assert response.status_code == 200, response.text
    payload = _payload("ordinary")
    payload["previous_response_id"] = "resp_native_http"
    response = await async_client.post(path, json=payload, headers=headers)
    assert response.status_code == 200, response.text
    assert [account for account, _ in calls] == ["blue-in", "blue-in"]


@pytest.mark.parametrize("path", ROUTES[:3])
async def test_http_daybreak_non_streaming_collection(async_client, monkeypatch, path):
    headers = await _setup(async_client)
    calls = _install_upstream(monkeypatch)
    payload = _payload("native")
    payload["stream"] = False
    response = await async_client.post(path, json=payload, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["id"] == "resp_native_http"
    assert [account for account, _ in calls] == ["blue-in"]


async def test_http_daybreak_terminal_compaction_preserves_native_selection(async_client, monkeypatch):
    headers = await _setup(async_client)
    calls = _install_upstream(monkeypatch)
    payload = _payload("native")
    payload["input"] = [{"role": "user", "content": "summarize"}, {"type": "compaction_trigger"}]
    response = await async_client.post(ROUTES[0], json=payload, headers=headers)
    assert response.status_code == 200, response.text
    assert "compaction" in response.text
    assert [account for account, _ in calls] == ["blue-in"]
    assert calls[0][1]["access_programs"] == payload["access_programs"]


@pytest.mark.parametrize("path", [ROUTES[0], ROUTES[3]])
async def test_http_daybreak_disabled_key_keeps_ordinary_scope(async_client, monkeypatch, path):
    headers = await _setup(async_client, scoped_to_ordinary=True, enabled=False)
    calls = _install_upstream(monkeypatch, forbid_sources=False)
    response = await async_client.post(path, json=_payload("native"), headers=headers)
    assert response.status_code == 200, response.text
    assert [account for account, _ in calls] == ["ordinary"]


@pytest.mark.parametrize("path", [ROUTES[0], ROUTES[3]])
@pytest.mark.parametrize(
    "selectors",
    [
        '"access_programs":{"cyber":"daybreak_blue"},"access_programs":{"cyber":"standard"}',
        '"access_programs":{"cyber":"daybreak_blue","cyber":"standard"}',
        '"model":"gpt-daybreak-blue-latest","model":"gpt-5.1"',
    ],
)
async def test_http_daybreak_duplicate_native_selectors_fail_before_dispatch(
    async_client, monkeypatch, path, selectors
):
    headers = await _setup(async_client)
    calls = _install_upstream(monkeypatch)
    body = '{"instructions":"","input":"test","model":"gpt-5.1",' + selectors + "}"
    response = await async_client.post(path, content=body, headers={**headers, "Content-Type": "application/json"})
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "unsupported_access_program"
    assert calls == []


@pytest.mark.parametrize("path", [ROUTES[0], ROUTES[3]])
async def test_http_daybreak_retries_only_another_eligible_account(async_client, monkeypatch, path):
    headers = await _setup(async_client, allow_second_blue=True)
    calls = _install_upstream(monkeypatch, reject_first_account=True)
    response = await async_client.post(path, json=_payload("native"), headers=headers)
    assert response.status_code == 200, response.text
    assert "response.failed" not in response.text
    assert {account for account, _ in calls} == {"blue-in", "blue-out"}
    assert calls[-1][0] != calls[0][0]
    assert all(payload["access_programs"] == {"cyber": "daybreak_blue"} for _, payload in calls)


@pytest.mark.parametrize("path", [ROUTES[0], ROUTES[3]])
async def test_http_daybreak_lineage_failure_releases_reservation_without_ordinary_fallback(
    async_client, monkeypatch, path
):
    from app.modules.proxy.capability_routing import CapabilityRouter, _capability_lineage_unavailable_error

    headers = await _setup(async_client, with_limit=True)
    calls = _install_upstream(monkeypatch)
    original_route = CapabilityRouter.route
    original_stream = proxy_service.core_stream_responses
    closed = []
    route_calls = 0

    async def fail_response_registration(self, *args, **kwargs):
        nonlocal route_calls
        route_calls += 1
        if route_calls > 1:
            raise _capability_lineage_unavailable_error()
        return await original_route(self, *args, **kwargs)

    async def closing_stream(*args, **kwargs):
        try:
            async for block in original_stream(*args, **kwargs):
                yield block
        finally:
            closed.append(True)

    monkeypatch.setattr(CapabilityRouter, "route", fail_response_registration)
    monkeypatch.setattr(proxy_service, "core_stream_responses", closing_stream)
    response = await async_client.post(path, json=_payload("native"), headers=headers)
    assert response.status_code >= 400 or "response.failed" in response.text, response.text
    assert "capability_lineage_unavailable" in response.text
    assert [account for account, _ in calls] == ["blue-in"]
    if not path.endswith("compact"):
        assert closed == [True]
    async with SessionLocal() as session:
        reservations = (await session.scalars(select(ApiKeyUsageReservation))).all()
        assert reservations
        assert all(reservation.status != "reserved" for reservation in reservations)


@pytest.mark.parametrize("path", [ROUTES[0], ROUTES[3]])
@pytest.mark.parametrize("case", ["eligible", "empty_scope", "standard_conflict", "disabled"])
async def test_http_enforced_daybreak_model_obeys_eligibility(async_client, monkeypatch, path, case):
    headers = await _setup(
        async_client, scoped_to_ordinary=case in {"empty_scope", "disabled"}, enabled=case != "disabled"
    )
    async with SessionLocal() as session:
        key = (await session.scalars(select(ApiKey))).one()
        key_id = key.id
    update = await async_client.patch("/api/api-keys/" + key_id, json={"enforcedModel": "gpt-daybreak-blue-latest"})
    assert update.status_code == 200, update.text
    calls = _install_upstream(monkeypatch, forbid_sources=case != "disabled")
    payload = _payload("ordinary")
    if case == "standard_conflict":
        payload["access_programs"] = {"cyber": "standard"}
    response = await async_client.post(path, json=payload, headers=headers)
    if case == "empty_scope":
        assert "no_security_work_authorized_accounts" in response.text
        assert calls == []
    elif case == "standard_conflict":
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == "unsupported_access_program"
        assert calls == []
    else:
        assert response.status_code == 200, response.text
        assert [account for account, _ in calls] == ["ordinary" if case == "disabled" else "blue-in"]
        assert calls[0][1]["model"] == "gpt-daybreak-blue-latest"


@pytest.mark.parametrize("path", [ROUTES[0], ROUTES[2], ROUTES[3], ROUTES[4]])
@pytest.mark.parametrize("identifier", ["native", "legacy"])
@pytest.mark.parametrize("conversation_header", ["x-codex-window-id", "thread-id"])
@pytest.mark.parametrize("ordinary_first", [False, True])
async def test_http_daybreak_conversation_isolation_preserves_burn_first(
    async_client, monkeypatch, path, identifier, conversation_header, ordinary_first
):
    headers = await _setup(async_client)
    calls = _install_upstream(monkeypatch, forbid_sources=False, unique_response_ids=True)
    ordinary_payload = _payload("ordinary")
    shared_headers = {**headers, "session_id": "shared-provider", "x-codex-parent-thread-id": "shared-task"}
    requests = [
        ("shared-task:2", _payload(identifier)),
        ("shared-task:1", ordinary_payload),
        ("shared-task:3", ordinary_payload),
        ("shared-task:2", {**ordinary_payload, "access_programs": {"cyber": "standard"}}),
    ]
    if ordinary_first:
        requests.insert(0, ("shared-task:1", ordinary_payload))
    for window, payload in requests:
        response = await async_client.post(path, json=payload, headers={**shared_headers, conversation_header: window})
        assert response.status_code == 200, response.text
    expected = ["blue-in", "ordinary", "ordinary", "blue-in"]
    assert [account for account, _ in calls] == (["ordinary", *expected] if ordinary_first else expected)


@pytest.mark.parametrize("path", [ROUTES[0], ROUTES[3]])
async def test_http_new_window_ignores_legacy_broad_markers(async_client, monkeypatch, path):
    headers = await _setup(async_client)
    calls = _install_upstream(monkeypatch, forbid_sources=False)
    async with SessionLocal() as session:
        key = (await session.scalars(select(ApiKey))).one()
        await CapabilityLineageRepository(session).require(
            capability="trusted_cyber",
            api_key_scope=key.id,
            aliases=(
                CapabilityLineageAlias(kind="codex_task", value="legacy-task"),
                CapabilityLineageAlias(kind="session_header", value="legacy-session"),
                CapabilityLineageAlias(kind="turn_state", value="legacy-turn"),
            ),
        )
        await session.commit()
    response = await async_client.post(
        path,
        json=_payload("ordinary"),
        headers={**headers, "session_id": "legacy-session", "x-codex-window-id": "legacy-task:4"},
    )
    assert response.status_code == 200, response.text
    assert [account for account, _ in calls] == ["ordinary"]


@pytest.mark.parametrize("with_owner_anchor", [False, True])
async def test_http_mid_conversation_daybreak_switch_preserves_account_ownership(
    async_client, monkeypatch, with_owner_anchor
):
    headers = await _setup(async_client)
    headers["x-codex-window-id"] = "switching-conversation:1"
    calls = _install_upstream(monkeypatch, forbid_sources=False, unique_response_ids=True)
    ordinary = _payload("ordinary")
    response = await async_client.post(ROUTES[0], json=ordinary, headers=headers)
    assert response.status_code == 200, response.text
    assert [account for account, _ in calls] == ["ordinary"]
    required = _payload("native")
    if with_owner_anchor:
        required["previous_response_id"] = "resp_native_http_1"
    response = await async_client.post(ROUTES[0], json=required, headers=headers)
    if with_owner_anchor:
        assert response.status_code >= 400 or "response.failed" in response.text, response.text
        assert [account for account, _ in calls] == ["ordinary"]
    else:
        assert response.status_code == 200, response.text
        response = await async_client.post(ROUTES[0], json=ordinary, headers=headers)
        assert response.status_code == 200, response.text
        assert [account for account, _ in calls] == ["ordinary", "blue-in", "blue-in"]
