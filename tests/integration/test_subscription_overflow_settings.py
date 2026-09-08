"""Subscription-overflow designation, preflight, and delete-clears behaviour (#2123 WP-B).

Everything here is dashboard-side. The last test pins the stage's inertness
end to end: with a source designated, an exhausted pool still answers today's
``429 usage_limit_reached`` and the source is never contacted.
"""

from __future__ import annotations

import asyncio
import base64
import json
import socket
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timedelta, timezone

import pytest
from aiohttp import web
from sqlalchemy import event, select
from sqlalchemy.orm import Session as SyncSession

import app.modules.proxy.service as proxy_module
from app.core.auth import generate_unique_account_id
from app.core.auth.dependencies import require_dashboard_write_access
from app.core.clients.proxy import ProxyResponseError
from app.core.config.settings_cache import get_settings_cache
from app.core.exceptions import DashboardPermissionError
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, AuditLog, ModelSourcePin
from app.db.session import SessionLocal
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.settings.repository import SettingsRepository
from app.modules.settings.subscription_overflow import DRAIN_WINDOW, PIN_IDLE_TTL
from app.modules.usage.repository import UsageRepository

pytestmark = pytest.mark.integration

_DRAIN_TOLERANCE = timedelta(minutes=5)
_REGISTRY_SLUG = "gpt-5.4"
_LITE_SLUG = "gpt-5.6-sol"


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


async def _import_account(async_client, account_id: str, email: str) -> str:
    auth_json = {
        "tokens": {
            "idToken": _encode_jwt(
                {
                    "email": email,
                    "chatgpt_account_id": account_id,
                    "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
                }
            ),
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "accountId": account_id,
        },
    }
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200
    return generate_unique_account_id(account_id, email)


async def _create_model_source(
    async_client,
    *,
    name: str,
    supports_responses: bool = True,
    base_url: str = "http://127.0.0.1:9/v1",
    models: list[dict[str, object]] | None = None,
) -> str:
    response = await async_client.post(
        "/api/model-sources/",
        json={
            "name": name,
            "baseUrl": base_url,
            "apiKey": f"token-{name}",
            "supportsChatCompletions": True,
            "supportsResponses": supports_responses,
            "models": models
            if models is not None
            else [{"model": _REGISTRY_SLUG, "contextWindow": 400_000, "supportsStreaming": True}],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


async def _get_settings(async_client) -> dict:
    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    return response.json()


def _parse_drain(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


def _assert_drain_armed(value: str | None) -> datetime:
    armed = _parse_drain(value)
    assert armed is not None
    expected = utcnow() + DRAIN_WINDOW
    assert abs(armed - expected) < _DRAIN_TOLERANCE, (armed, expected)
    return armed


def _assert_drain_armed_in(settings: dict) -> datetime:
    """The deadline is armed and the derived pin expiry sits 22 days before it (clear time + 7 d)."""
    armed = _assert_drain_armed(settings["subscriptionOverflowDrainUntil"])
    pins_expire_by = _parse_drain(settings["subscriptionOverflowPinsExpireBy"])
    assert pins_expire_by == armed - (DRAIN_WINDOW - PIN_IDLE_TTL), (pins_expire_by, armed)
    return armed


def _assert_not_draining(settings: dict) -> None:
    assert settings["subscriptionOverflowDrainUntil"] is None
    assert settings["subscriptionOverflowPinsExpireBy"] is None


async def _wait_for_audit_log(action: str, *, attempts: int = 20) -> AuditLog:
    for _ in range(attempts):
        async with SessionLocal() as session:
            result = await session.execute(
                select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id.desc())
            )
            row = result.scalars().first()
            if row is not None:
                return row
        await asyncio.sleep(0.05)
    raise AssertionError(f"audit log not written for action={action}")


# ---------------------------------------------------------------------------
# Designation tri-state and drain deadline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_designation_round_trips_and_clearing_arms_the_drain_deadline(async_client):
    source_id = await _create_model_source(async_client, name="overflow-a")

    designated = await async_client.put("/api/settings", json={"subscriptionOverflowSourceId": source_id})
    assert designated.status_code == 200
    assert designated.json()["subscriptionOverflowSourceId"] == source_id
    _assert_not_draining(designated.json())
    fetched = await _get_settings(async_client)
    assert fetched["subscriptionOverflowSourceId"] == source_id
    _assert_not_draining(fetched)

    cleared = await async_client.put("/api/settings", json={"subscriptionOverflowSourceId": None})
    assert cleared.status_code == 200
    assert cleared.json()["subscriptionOverflowSourceId"] is None
    armed = _assert_drain_armed_in(cleared.json())
    _assert_drain_armed_in(await _get_settings(async_client))

    # NULL -> NULL never moves an armed deadline.
    repeated = await async_client.put("/api/settings", json={"subscriptionOverflowSourceId": None})
    assert repeated.status_code == 200
    assert _parse_drain(repeated.json()["subscriptionOverflowDrainUntil"]) == armed
    assert _parse_drain(repeated.json()["subscriptionOverflowPinsExpireBy"]) == armed - (DRAIN_WINDOW - PIN_IDLE_TTL)

    # Re-designating during the drain clears the deadline and the derived expiry.
    redesignated = await async_client.put("/api/settings", json={"subscriptionOverflowSourceId": source_id})
    assert redesignated.status_code == 200
    assert redesignated.json()["subscriptionOverflowSourceId"] == source_id
    _assert_not_draining(redesignated.json())


@pytest.mark.asyncio
async def test_designation_rejects_chat_only_and_unknown_sources(async_client):
    chat_only = await _create_model_source(async_client, name="chat-only", supports_responses=False)

    rejected = await async_client.put("/api/settings", json={"subscriptionOverflowSourceId": chat_only})
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "subscription_overflow_source_invalid"

    unknown = await async_client.put("/api/settings", json={"subscriptionOverflowSourceId": "src_missing"})
    assert unknown.status_code == 400
    assert unknown.json()["error"]["code"] == "subscription_overflow_source_invalid"

    assert (await _get_settings(async_client))["subscriptionOverflowSourceId"] is None


@pytest.mark.asyncio
async def test_designation_survives_partial_updates_and_switching_sources_keeps_the_deadline(async_client):
    source_a = await _create_model_source(async_client, name="overflow-a")
    source_b = await _create_model_source(async_client, name="overflow-b")
    assert (await async_client.put("/api/settings", json={"subscriptionOverflowSourceId": source_a})).status_code == 200

    # Omitting the field leaves the designation alone (tri-state via model_fields_set).
    partial = await async_client.put("/api/settings", json={"warmupModel": "gpt-5.4-pro"})
    assert partial.status_code == 200
    assert partial.json()["warmupModel"] == "gpt-5.4-pro"
    assert partial.json()["subscriptionOverflowSourceId"] == source_a
    assert partial.json()["subscriptionOverflowDrainUntil"] is None

    # Re-sending the current designation (the dashboard's full PUT) is a no-op.
    same = await async_client.put("/api/settings", json={"subscriptionOverflowSourceId": source_a})
    assert same.status_code == 200
    assert same.json()["subscriptionOverflowSourceId"] == source_a

    # non-NULL -> different non-NULL switches without touching the deadline.
    switched = await async_client.put("/api/settings", json={"subscriptionOverflowSourceId": source_b})
    assert switched.status_code == 200
    assert switched.json()["subscriptionOverflowSourceId"] == source_b
    assert switched.json()["subscriptionOverflowDrainUntil"] is None


@pytest.mark.asyncio
async def test_designation_put_honours_the_expected_version_cas(async_client):
    source_id = await _create_model_source(async_client, name="overflow-a")
    version = (await _get_settings(async_client))["version"]

    accepted = await async_client.put(
        "/api/settings",
        json={"expectedVersion": version, "subscriptionOverflowSourceId": source_id},
    )
    assert accepted.status_code == 200
    assert accepted.json()["version"] > version

    stale = await async_client.put(
        "/api/settings",
        json={"expectedVersion": version, "subscriptionOverflowSourceId": None},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "settings_conflict"
    assert (await _get_settings(async_client))["subscriptionOverflowSourceId"] == source_id


# ---------------------------------------------------------------------------
# Deleting the designated source (kill switch 3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deleting_the_designated_source_clears_the_designation_and_arms_the_drain(async_client):
    designated = await _create_model_source(async_client, name="overflow-a")
    other = await _create_model_source(async_client, name="overflow-b")
    assert (
        await async_client.put("/api/settings", json={"subscriptionOverflowSourceId": designated})
    ).status_code == 200

    # Unknown source: 404 and the designation is untouched.
    missing = await async_client.delete("/api/model-sources/src_missing")
    assert missing.status_code == 404
    assert (await _get_settings(async_client))["subscriptionOverflowSourceId"] == designated

    # A different source: deleted, designation untouched.
    assert (await async_client.delete(f"/api/model-sources/{other}")).status_code == 204
    untouched = await _get_settings(async_client)
    assert untouched["subscriptionOverflowSourceId"] == designated
    assert untouched["subscriptionOverflowDrainUntil"] is None
    other_audit = await _wait_for_audit_log("model_source_deleted")
    assert other_audit.details is not None
    assert json.loads(other_audit.details) == {"source_id": other, "subscription_overflow_cleared": False}

    # The designated source: deleted, designation cleared, drain armed.
    assert (await async_client.delete(f"/api/model-sources/{designated}")).status_code == 204
    cleared = await _get_settings(async_client)
    assert cleared["subscriptionOverflowSourceId"] is None
    _assert_drain_armed_in(cleared)
    for _ in range(20):
        designated_audit = await _wait_for_audit_log("model_source_deleted")
        if designated_audit.id != other_audit.id:
            break
        await asyncio.sleep(0.05)
    assert designated_audit.details is not None
    assert json.loads(designated_audit.details) == {"source_id": designated, "subscription_overflow_cleared": True}
    listed = await async_client.get("/api/model-sources/")
    assert listed.status_code == 200
    assert listed.json()["sources"] == []


@pytest.mark.asyncio
async def test_deleting_the_designated_source_invalidates_the_settings_cache_after_the_commit(
    async_client, monkeypatch
):
    designated = await _create_model_source(async_client, name="overflow-a")
    other = await _create_model_source(async_client, name="overflow-b")
    assert (
        await async_client.put("/api/settings", json={"subscriptionOverflowSourceId": designated})
    ).status_code == 200

    cache = get_settings_cache()
    original_invalidate = cache.invalidate
    original_clear = SettingsRepository.clear_subscription_overflow_source_if_matches
    sequence: list[str] = []
    request_sessions: list[SyncSession] = []

    # Both spies are scoped to the request under test. The app lifespan runs the
    # cache-invalidation poller, whose ``settings`` callback replays the PUT's
    # bump as ``invalidate(propagate=False)`` on some later tick, and the audit
    # writer commits from its own session in the background; either can land
    # inside this window, so only the route's propagating invalidation and the
    # request session's own commit are recorded.
    async def _spy_clear(self: SettingsRepository, source_id: str, **kwargs):
        request_sessions.append(self._session.sync_session)
        return await original_clear(self, source_id, **kwargs)

    def _after_commit(session: SyncSession) -> None:
        if any(session is candidate for candidate in request_sessions):
            sequence.append("commit")

    async def _spy_invalidate(*args, **kwargs):
        if kwargs.get("propagate", True):
            sequence.append("invalidate")
        return await original_invalidate(*args, **kwargs)

    monkeypatch.setattr(SettingsRepository, "clear_subscription_overflow_source_if_matches", _spy_clear)
    monkeypatch.setattr(cache, "invalidate", _spy_invalidate)
    event.listen(SyncSession, "after_commit", _after_commit)
    try:
        assert (await async_client.delete(f"/api/model-sources/{other}")).status_code == 204
        assert sequence == ["commit"], (
            "deleting a non-designated source commits once and never bumps the settings cache",
            sequence,
        )
        sequence.clear()
        # Deterministic stand-ins for the background traffic described above:
        # a poller-style local invalidation and an unrelated session's commit
        # must both be invisible to the spies.
        await cache.invalidate(propagate=False)
        async with SessionLocal() as unrelated:
            await unrelated.commit()
        assert (await async_client.delete(f"/api/model-sources/{designated}")).status_code == 204
    finally:
        event.remove(SyncSession, "after_commit", _after_commit)

    # The clear + delete commit lands first; the single propagating invalidation
    # (and its cross-replica bump) must follow it so peers re-read the cleared row.
    assert sequence == ["commit", "invalidate"], sequence


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_requires_write_access_and_reports_unknown_sources(app_instance, async_client):
    unknown = await async_client.get("/api/settings/subscription-overflow/preflight", params={"source_id": "src_x"})
    assert unknown.status_code == 404

    missing_param = await async_client.get("/api/settings/subscription-overflow/preflight")
    assert missing_param.status_code == 422

    source_id = await _create_model_source(async_client, name="overflow-a")

    async def reject_write_access() -> None:
        raise DashboardPermissionError(
            "Read-only dashboard access cannot modify dashboard state", code="read_only_access"
        )

    app_instance.dependency_overrides[require_dashboard_write_access] = reject_write_access
    try:
        forbidden = await async_client.get(
            "/api/settings/subscription-overflow/preflight", params={"source_id": source_id}
        )
    finally:
        app_instance.dependency_overrides.pop(require_dashboard_write_access, None)
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "read_only_access"


@pytest.mark.asyncio
async def test_preflight_reports_blockers_for_a_chat_only_source_without_failing(async_client):
    chat_only = await _create_model_source(async_client, name="chat-only", supports_responses=False)

    response = await async_client.get("/api/settings/subscription-overflow/preflight", params={"source_id": chat_only})
    assert response.status_code == 200
    payload = response.json()
    assert payload["sourceId"] == chat_only
    assert payload["sourceName"] == "chat-only"
    assert payload["sourceEnabled"] is True
    assert payload["eligible"] is False
    assert payload["blockers"] == ["source_responses_unsupported"]
    assert [model["slug"] for model in payload["servedModels"]] == [_REGISTRY_SLUG]
    assert payload["drainUntil"] is None


@pytest.mark.asyncio
async def test_preflight_reports_served_missing_warnings_scoped_keys_and_pins(async_client):
    source_id = await _create_model_source(
        async_client,
        name="overflow-a",
        models=[
            {
                "model": _REGISTRY_SLUG,
                "contextWindow": 8192,
                "maxOutputTokens": 1024,
                "supportsStreaming": True,
                "supportsVision": False,
                "inputPer1M": 0.5,
                "rawMetadataJson": json.dumps(
                    {"supports_search_tool": True, "experimental_supported_tools": ["custom", "apply_patch"]}
                ),
            },
            {"model": _LITE_SLUG, "contextWindow": 400_000, "supportsVision": True},
            {"model": "local-only", "contextWindow": 32_000, "isEnabled": False},
        ],
    )

    response = await async_client.get("/api/settings/subscription-overflow/preflight", params={"source_id": source_id})
    assert response.status_code == 200
    payload = response.json()
    assert payload["eligible"] is True
    assert payload["blockers"] == []
    served = {model["slug"]: model for model in payload["servedModels"]}
    assert set(served) == {_REGISTRY_SLUG, _LITE_SLUG, "local-only"}

    registry_model = served[_REGISTRY_SLUG]
    assert registry_model["enabled"] is True
    assert registry_model["neverOverflows"] is False
    assert registry_model["undeclaredToolTypes"] == ["local_shell", "shell", "tool_search"]
    assert registry_model["supportsVision"] is False
    assert registry_model["supportsStreaming"] is True
    assert registry_model["priced"] is False
    assert registry_model["contextWindowMismatch"] == {"registry": 272_000, "source": 8192, "maxOutputTokens": 1024}
    assert registry_model["warnings"] == ["undeclared_tool_types", "no_vision", "unpriced", "context_window_smaller"]

    lite_model = served[_LITE_SLUG]
    assert lite_model["neverOverflows"] is True
    assert lite_model["neverOverflowsReason"] == "responses_lite"
    assert lite_model["warnings"] == ["responses_lite_excluded"]

    local_model = served["local-only"]
    assert local_model["enabled"] is False
    assert local_model["neverOverflowsReason"] == "not_in_registry"

    # Bootstrap registry: the Lite family is excluded from "missing"; other
    # subscription slugs the source does not enable are listed.
    assert _REGISTRY_SLUG not in payload["missingModels"]
    assert not any(slug.startswith("gpt-5.6") for slug in payload["missingModels"])
    assert {"gpt-5.5", "gpt-5.4-mini"} <= set(payload["missingModels"])
    assert payload["scopedApiKeyCount"] == 0
    assert payload["livePinCount"] == 0
    assert payload["tombstoneCount"] == 0

    # Source-scoped API key and pin rows are counted.
    enable = await async_client.put("/api/settings", json={"apiKeyAuthEnabled": True})
    assert enable.status_code == 200
    created = await async_client.post(
        "/api/api-keys/",
        json={"name": "scoped-key", "assignedSourceIds": [source_id]},
    )
    assert created.status_code == 200, created.text
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        session.add_all(
            [
                ModelSourcePin(
                    pin_key="thread\nlive",
                    kind="thread",
                    source_id=source_id,
                    created_at=now,
                    last_seen_at=now,
                    expires_at=now + timedelta(days=1),
                    purge_at=now + timedelta(days=22),
                ),
                ModelSourcePin(
                    pin_key="thread\ntombstone",
                    kind="thread",
                    source_id=source_id,
                    created_at=now - timedelta(days=8),
                    last_seen_at=now - timedelta(days=8),
                    expires_at=now - timedelta(days=1),
                    purge_at=now + timedelta(days=20),
                ),
                ModelSourcePin(
                    pin_key="thread\npurged",
                    kind="thread",
                    source_id=source_id,
                    created_at=now - timedelta(days=40),
                    last_seen_at=now - timedelta(days=40),
                    expires_at=now - timedelta(days=30),
                    purge_at=now - timedelta(days=9),
                ),
                ModelSourcePin(
                    pin_key="bounce\nlive",
                    kind="bounce",
                    source_id=source_id,
                    created_at=now,
                    last_seen_at=now,
                    expires_at=now + timedelta(seconds=60),
                    purge_at=now + timedelta(seconds=60),
                ),
                ModelSourcePin(
                    pin_key="thread\nother-source",
                    kind="thread",
                    source_id="src_other",
                    created_at=now,
                    last_seen_at=now,
                    expires_at=now + timedelta(days=1),
                    purge_at=now + timedelta(days=22),
                ),
            ]
        )
        await session.commit()

    response = await async_client.get("/api/settings/subscription-overflow/preflight", params={"source_id": source_id})
    assert response.status_code == 200
    payload = response.json()
    assert payload["scopedApiKeyCount"] == 1
    assert payload["livePinCount"] == 1
    assert payload["tombstoneCount"] == 1


@pytest.mark.asyncio
async def test_preflight_echoes_the_drain_deadline(async_client):
    source_id = await _create_model_source(async_client, name="overflow-a")
    assert (
        await async_client.put("/api/settings", json={"subscriptionOverflowSourceId": source_id})
    ).status_code == 200
    assert (await async_client.put("/api/settings", json={"subscriptionOverflowSourceId": None})).status_code == 200

    response = await async_client.get("/api/settings/subscription-overflow/preflight", params={"source_id": source_id})
    assert response.status_code == 200
    _assert_drain_armed(response.json()["drainUntil"])


# ---------------------------------------------------------------------------
# Inert end to end: the designation never changes today's exhaustion answer
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


_UpstreamHandler = Callable[[web.Request], Awaitable[web.StreamResponse]]


@pytest.fixture
async def source_upstream() -> AsyncIterator[Callable[[_UpstreamHandler], Awaitable[str]]]:
    runners: list[web.AppRunner] = []

    async def start(handler: _UpstreamHandler) -> str:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        port = _free_port()
        site = web.TCPSite(runner, "127.0.0.1", port)
        await site.start()
        runners.append(runner)
        return f"http://127.0.0.1:{port}/v1"

    yield start

    for runner in runners:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_designated_source_is_inert_when_the_pool_is_exhausted(async_client, source_upstream, monkeypatch):
    """Golden guard for WP-C2: a designated source must not change today's exhaustion answer.

    The pool is usage-proven exhausted (QUOTA_EXCEEDED at 100 % with a known
    reset), the requested model is a registry slug the designated source
    serves, and the request still gets the structured ``429 usage_limit_reached``
    with ``resets_at`` while the source receives nothing. WP-C2 replaces this
    test when overflow routing becomes reachable.
    """
    account_id = await _import_account(async_client, "acc_overflow_inert", "overflow-inert@example.com")
    now_epoch = int(time.time())
    reset_at = now_epoch + 1800
    now = utcnow()
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        # Mirror handle_quota_exceeded: status + blocked_at marker + reset deadline.
        account.status = AccountStatus.QUOTA_EXCEEDED
        account.blocked_at = now_epoch
        account.reset_at = reset_at
        usage = UsageRepository(session)
        await usage.add_entry(
            account_id=account_id,
            used_percent=100.0,
            window="primary",
            reset_at=reset_at,
            window_minutes=300,
            recorded_at=now,
        )
        await usage.add_entry(
            account_id=account_id,
            used_percent=40.0,
            window="secondary",
            reset_at=reset_at + 6 * 86400,
            window_minutes=10080,
            recorded_at=now,
        )
        await session.commit()
    get_account_selection_cache().invalidate()

    hits: list[str] = []

    async def record(request: web.Request) -> web.StreamResponse:
        hits.append(request.path)
        return web.json_response({"error": {"message": "source must never be contacted"}}, status=500)

    base_url = await source_upstream(record)
    source_id = await _create_model_source(async_client, name="overflow-stub", base_url=base_url)
    designated = await async_client.put("/api/settings", json={"subscriptionOverflowSourceId": source_id})
    assert designated.status_code == 200

    subscription_attempts: list[str] = []

    async def fail_fast_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        # Exhaustion must be decided before any upstream call; fail loudly
        # instead of letting a mis-seeded pool hang on the unreachable upstream.
        subscription_attempts.append(account_id)
        raise ProxyResponseError(500, {"error": {"message": "unexpected subscription attempt"}})
        yield  # pragma: no cover - async generator marker

    monkeypatch.setattr(proxy_module, "core_stream_responses", fail_fast_stream)

    payload = {"model": _REGISTRY_SLUG, "instructions": "hi", "input": [], "stream": True}
    response = await async_client.post("/backend-api/codex/responses", json=payload)
    assert subscription_attempts == [], "an exhausted pool must not attempt a subscription stream"
    assert response.status_code == 429
    error = response.json()["error"]
    assert error["code"] == "usage_limit_reached"
    assert error["type"] == "usage_limit_reached"
    assert error["resets_at"] == reset_at
    assert hits == [], "the designated source must not be contacted before WP-C2"

    # The designation itself is untouched by request traffic.
    settings = await _get_settings(async_client)
    assert settings["subscriptionOverflowSourceId"] == source_id
    assert settings["subscriptionOverflowDrainUntil"] is None
