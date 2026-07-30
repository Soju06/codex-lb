from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from urllib.parse import quote

import pytest
from sqlalchemy import event, select, update

import app.core.auth.dependencies as auth_dependencies
from app.core.auth.dashboard_access import guest_principal
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, ApiKey, RequestKind, RequestLog
from app.db.session import SessionLocal, engine
from app.modules.accounts.repository import AccountsRepository
from app.modules.request_logs.repository import RequestLogsRepository

pytestmark = pytest.mark.integration


def _assert_no_seeded_sensitive_values(response, values: tuple[str, ...]) -> None:
    serialized = response.text
    assert all(value not in serialized for value in values)


def _distinct_candidate_fragments(statements: list[str]) -> list[str]:
    return [
        match.group(0)
        for statement in statements
        for match in re.finditer(
            r"SELECT DISTINCT REQUEST_LOGS\.CONVERSATION_ID.*?\)\s+AS",
            statement.upper(),
            flags=re.DOTALL,
        )
    ]


def _account(account_id: str) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


@dataclass(frozen=True, slots=True)
class _ConversationSeed:
    request_id: str
    account_id: str
    model: str
    reasoning_effort: str | None
    input_tokens: int
    output_tokens: int | None
    cached_input_tokens: int | None
    reasoning_tokens: int
    latency_ms: int | None
    cost_usd: float
    requested_at: datetime
    conversation_id: str
    api_key_id: str | None = None
    useragent_group: str | None = None


async def _seed_conversations(base):
    async with SessionLocal() as session:
        accounts = AccountsRepository(session)
        logs = RequestLogsRepository(session)
        await accounts.upsert(_account("acc-a"))
        await accounts.upsert(_account("acc-b"))
        session.add_all(
            [
                ApiKey(id="key-a", name="Alpha dashboard key", key_hash="secret-a", key_prefix="sk-a"),
                ApiKey(id="key-z", name="Zeta dashboard key", key_hash="secret-z", key_prefix="sk-z"),
            ]
        )
        await session.commit()

        rows = [
            # Search matches this row's UA, but the other eligible rows must
            # still contribute to the whole-conversation aggregate.
            _ConversationSeed(
                request_id="a-1",
                account_id="acc-a",
                model="model-a",
                reasoning_effort="high",
                input_tokens=100,
                output_tokens=50,
                cached_input_tokens=200,
                reasoning_tokens=0,
                latency_ms=100,
                cost_usd=1.0,
                requested_at=base - timedelta(minutes=3),
                conversation_id="Conv-A",
                api_key_id="key-a",
                useragent_group="Editor",
            ),
            _ConversationSeed(
                request_id="a-2",
                account_id="acc-b",
                model="model-a",
                reasoning_effort="low",
                input_tokens=40,
                output_tokens=None,
                cached_input_tokens=-5,
                reasoning_tokens=20,
                latency_ms=50,
                cost_usd=2.0,
                requested_at=base - timedelta(minutes=2),
                conversation_id="Conv-A",
                api_key_id="key-z",
                useragent_group="Other",
            ),
            _ConversationSeed(
                request_id="a-3",
                account_id="acc-a",
                model="model-b",
                reasoning_effort=None,
                input_tokens=10,
                output_tokens=5,
                cached_input_tokens=None,
                reasoning_tokens=0,
                latency_ms=None,
                cost_usd=3.0,
                requested_at=base - timedelta(minutes=1),
                conversation_id="Conv-A",
                api_key_id="key-z",
                useragent_group="Other",
            ),
            _ConversationSeed(
                request_id="b-1",
                account_id="acc-b",
                model="model-c",
                reasoning_effort="medium",
                input_tokens=1,
                output_tokens=1,
                cached_input_tokens=0,
                reasoning_tokens=0,
                latency_ms=7,
                cost_usd=0.1,
                requested_at=base - timedelta(minutes=1),
                conversation_id="conv-b",
                useragent_group="Terminal",
            ),
        ]
        for row in rows:
            await logs.add_log(
                account_id=row.account_id,
                request_id=row.request_id,
                model=row.model,
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
                latency_ms=row.latency_ms,
                status="success",
                error_code=None,
                requested_at=row.requested_at,
                cached_input_tokens=row.cached_input_tokens,
                reasoning_tokens=row.reasoning_tokens,
                reasoning_effort=row.reasoning_effort,
                api_key_id=row.api_key_id,
                useragent_group=row.useragent_group,
                conversation_id=row.conversation_id,
                cost_usd=row.cost_usd,
            )


async def _seed_conversation_rows(rows: list[_ConversationSeed]) -> None:
    async with SessionLocal() as session:
        accounts = AccountsRepository(session)
        logs = RequestLogsRepository(session)
        for account_id in {row.account_id for row in rows}:
            await accounts.upsert(_account(account_id))
        for row in rows:
            await logs.add_log(
                account_id=row.account_id,
                request_id=row.request_id,
                model=row.model,
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
                latency_ms=row.latency_ms,
                status="success",
                error_code=None,
                requested_at=row.requested_at,
                cached_input_tokens=row.cached_input_tokens,
                reasoning_tokens=row.reasoning_tokens,
                reasoning_effort=row.reasoning_effort,
                api_key_id=row.api_key_id,
                useragent_group=row.useragent_group,
                conversation_id=row.conversation_id,
                cost_usd=row.cost_usd,
            )


@pytest.mark.asyncio
async def test_conversation_list_contract_search_and_aggregate(async_client, db_setup):
    base = utcnow().replace(microsecond=0)
    await _seed_conversations(base)

    response = await async_client.get("/api/conversations?search=EDITOR&limit=1")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"conversations", "total", "hasMore"}
    assert body["total"] == 1
    assert body["hasMore"] is False
    entry = body["conversations"][0]
    assert set(entry) == {
        "conversationId",
        "firstRequest",
        "lastRequest",
        "requestCount",
        "representativeAccount",
        "remainingAccountCount",
        "apiKeyId",
        "apiKeyName",
        "representativeModel",
        "remainingModelCount",
        "totalTokens",
        "cachedInputTokens",
        "totalCostUsd",
    }
    assert entry["conversationId"] == "Conv-A"
    assert entry["requestCount"] == 3
    assert entry["firstRequest"].startswith((base - timedelta(minutes=3)).isoformat())
    assert entry["lastRequest"].startswith((base - timedelta(minutes=1)).isoformat())
    assert entry["representativeAccount"] == "acc-a"
    assert entry["remainingAccountCount"] == 1
    assert entry["apiKeyId"] == "key-z"
    assert entry["apiKeyName"] == "Zeta dashboard key"
    assert entry["representativeModel"] == "model-a"
    assert entry["remainingModelCount"] == 1
    assert entry["totalTokens"] == 225
    assert entry["cachedInputTokens"] == 100
    assert entry["totalCostUsd"] == pytest.approx(6.0)
    _assert_no_seeded_sensitive_values(response, ("secret-a", "secret-z", "sk-a", "sk-z"))


@pytest.mark.asyncio
async def test_conversation_list_pagination_and_stable_order(async_client, db_setup):
    base = utcnow().replace(microsecond=0)
    await _seed_conversations(base)

    response = await async_client.get("/api/conversations?limit=1&offset=1")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["hasMore"] is False
    assert [row["conversationId"] for row in body["conversations"]] == ["conv-b"]


@pytest.mark.asyncio
async def test_conversation_list_trailing_slash_preserves_collection_route(async_client, db_setup):
    base = utcnow().replace(microsecond=0)
    await _seed_conversations(base)
    params = {"limit": 1, "since": (base - timedelta(days=1)).isoformat()}

    without_slash = await async_client.get("/api/conversations", params=params)
    with_slash = await async_client.get("/api/conversations/", params=params)

    assert without_slash.status_code == 200
    assert with_slash.status_code == 200
    assert with_slash.json() == without_slash.json()


@pytest.mark.asyncio
async def test_conversation_list_excludes_blank_warmup_limit_warmup_and_deleted(async_client, db_setup):
    base = utcnow().replace(microsecond=0)
    await _seed_conversations(base)
    async with SessionLocal() as session:
        logs = RequestLogsRepository(session)
        for request_id, conversation_id, request_kind in (
            ("null", None, RequestKind.NORMAL.value),
            ("blank", "   ", RequestKind.NORMAL.value),
            ("warmup", "Conv-A", RequestKind.WARMUP.value),
            ("limit-warmup", "Conv-A", "limit_warmup"),
            ("deleted", "Conv-A", RequestKind.NORMAL.value),
        ):
            await logs.add_log(
                account_id="acc-a",
                request_id=request_id,
                model="excluded",
                input_tokens=999,
                output_tokens=999,
                latency_ms=999,
                status="success",
                error_code=None,
                requested_at=base + timedelta(minutes=1),
                conversation_id=conversation_id,
                request_kind=request_kind,
            )
        assert await session.scalar(select(RequestLog.conversation_id).where(RequestLog.request_id == "blank")) is None
        await session.execute(update(RequestLog).where(RequestLog.request_id == "deleted").values(deleted_at=base))
        await session.commit()

    response = await async_client.get("/api/conversations")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert {row["conversationId"] for row in body["conversations"]} == {"Conv-A", "conv-b"}
    conv_a = next(row for row in body["conversations"] if row["conversationId"] == "Conv-A")
    assert conv_a["requestCount"] == 3
    assert conv_a["firstRequest"].startswith((base - timedelta(minutes=3)).isoformat())
    assert conv_a["totalTokens"] == 225
    assert conv_a["lastRequest"].startswith((base - timedelta(minutes=1)).isoformat())


@pytest.mark.asyncio
async def test_conversation_list_searches_normalized_id_case_insensitively(async_client, db_setup):
    base = utcnow().replace(microsecond=0)
    await _seed_conversations(base)

    response = await async_client.get("/api/conversations?search=conv-a")
    assert response.status_code == 200
    assert [row["conversationId"] for row in response.json()["conversations"]] == ["Conv-A"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("search", "expected_ids"),
    [
        ("thread_", ["thread_"]),
        ("thread%", ["thread%literal"]),
        ("thread\\", [r"thread\literal"]),
        ("agent_", ["ua-underscore"]),
        ("agent%", ["ua-percent"]),
        ("agent\\", ["ua-backslash"]),
    ],
)
async def test_conversation_list_search_treats_like_wildcards_as_literal(
    async_client, db_setup, search: str, expected_ids: list[str]
):
    base = utcnow().replace(microsecond=0)
    async with SessionLocal() as session:
        logs = RequestLogsRepository(session)
        rows = [
            ("thread_", "ordinary-agent"),
            ("threadX", "ordinary-agent"),
            ("thread%literal", "ordinary-agent"),
            (r"thread\literal", "ordinary-agent"),
            ("ua-underscore", "agent_"),
            ("ua-wildcard", "agentX"),
            ("ua-percent", "agent%"),
            ("ua-backslash", r"agent\family"),
        ]
        for index, (conversation_id, useragent_group) in enumerate(rows):
            await logs.add_log(
                account_id=None,
                request_id=f"like-literal-{index}",
                model="model",
                input_tokens=1,
                output_tokens=1,
                latency_ms=1,
                status="success",
                error_code=None,
                requested_at=base,
                conversation_id=conversation_id,
                useragent_group=useragent_group,
            )

    response = await async_client.get("/api/conversations", params={"search": search})

    assert response.status_code == 200
    assert [row["conversationId"] for row in response.json()["conversations"]] == expected_ids


@pytest.mark.asyncio
async def test_conversation_list_percent_search_is_not_match_all(async_client, db_setup):
    base = utcnow().replace(microsecond=0)
    async with SessionLocal() as session:
        logs = RequestLogsRepository(session)
        await logs.add_log(
            account_id=None,
            request_id="literal-search-only",
            model="model",
            input_tokens=1,
            output_tokens=1,
            latency_ms=1,
            status="success",
            error_code=None,
            requested_at=base,
            conversation_id="ordinary-conversation",
            useragent_group="ordinary-agent",
        )

    response = await async_client.get("/api/conversations", params={"search": "%"})

    assert response.status_code == 200
    assert response.json() == {"conversations": [], "total": 0, "hasMore": False}


@pytest.mark.asyncio
async def test_conversation_list_unmatched_search_returns_exact_empty_envelope(async_client, db_setup):
    base = utcnow().replace(microsecond=0)
    await _seed_conversations(base)

    response = await async_client.get("/api/conversations?search=does-not-match")

    assert response.status_code == 200
    assert response.json() == {"conversations": [], "total": 0, "hasMore": False}


@pytest.mark.asyncio
async def test_conversation_list_api_key_representative_is_safe_and_deterministic(async_client, db_setup):
    base = utcnow().replace(microsecond=0)
    sensitive_values = (
        "hash-tie-key-a",
        "hash-tie-key-z",
        "plaintext-tie-key-a",
        "plaintext-tie-key-z",
    )
    async with SessionLocal() as session:
        logs = RequestLogsRepository(session)
        await logs.add_log(
            account_id=None,
            request_id="key-tie-null",
            model="key-model",
            input_tokens=1,
            output_tokens=1,
            latency_ms=1,
            status="success",
            error_code=None,
            requested_at=base,
            conversation_id="key-tie-conversation",
            api_key_id=None,
        )
        session.add_all(
            [
                ApiKey(
                    id="key-tie-a",
                    name="Alpha dashboard key",
                    key_hash="hash-tie-key-a",
                    key_prefix="plaintext-tie-key-a",
                ),
                ApiKey(
                    id="key-tie-z",
                    name="Zeta dashboard key",
                    key_hash="hash-tie-key-z",
                    key_prefix="plaintext-tie-key-z",
                ),
            ]
        )
        await session.commit()
        for request_id, api_key_id in (("key-tie-a", "key-tie-a"), ("key-tie-z", "key-tie-z")):
            await logs.add_log(
                account_id=None,
                request_id=request_id,
                model="key-model",
                input_tokens=1,
                output_tokens=1,
                latency_ms=1,
                status="success",
                error_code=None,
                requested_at=base,
                conversation_id="key-tie-conversation",
                api_key_id=api_key_id,
            )

    response = await async_client.get("/api/conversations")

    assert response.status_code == 200
    entry = response.json()["conversations"][0]
    assert entry["apiKeyId"] == "key-tie-a"
    assert entry["apiKeyName"] == "Alpha dashboard key"
    _assert_no_seeded_sensitive_values(response, sensitive_values)


@pytest.mark.asyncio
async def test_conversation_list_api_key_representative_ignores_stale_majority(async_client, db_setup):
    base = utcnow().replace(microsecond=0)
    stale_id = "stale-majority-key"
    safe_id = "safe-majority-key"
    stale_secret = "stale-majority-secret"
    async with SessionLocal() as session:
        logs = RequestLogsRepository(session)
        session.add(
            ApiKey(
                id=safe_id,
                name="Safe majority key",
                key_hash="safe-majority-hash",
                key_prefix="sk-safe-majority",
            )
        )
        await session.commit()
        for index in range(3):
            await logs.add_log(
                account_id=None,
                request_id=f"stale-majority-{index}",
                model="model",
                input_tokens=1,
                output_tokens=1,
                latency_ms=1,
                status="success",
                error_code=None,
                requested_at=base,
                conversation_id="stale-majority-conversation",
                api_key_id=stale_id,
            )
        await logs.add_log(
            account_id=None,
            request_id="safe-majority",
            model="model",
            input_tokens=1,
            output_tokens=1,
            latency_ms=1,
            status="success",
            error_code=None,
            requested_at=base,
            conversation_id="stale-majority-conversation",
            api_key_id=safe_id,
        )

    response = await async_client.get("/api/conversations")

    assert response.status_code == 200
    entry = response.json()["conversations"][0]
    assert entry["apiKeyId"] == safe_id
    assert entry["apiKeyName"] == "Safe majority key"
    _assert_no_seeded_sensitive_values(response, (stale_id, stale_secret, "safe-majority-hash"))


@pytest.mark.asyncio
async def test_conversation_list_api_key_representative_is_null_when_all_keys_stale(async_client, db_setup):
    base = utcnow().replace(microsecond=0)
    stale_id = "stale-only-key"
    async with SessionLocal() as session:
        logs = RequestLogsRepository(session)
        await logs.add_log(
            account_id=None,
            request_id="stale-only",
            model="model",
            input_tokens=1,
            output_tokens=1,
            latency_ms=1,
            status="success",
            error_code=None,
            requested_at=base,
            conversation_id="stale-only-conversation",
            api_key_id=stale_id,
        )

    response = await async_client.get("/api/conversations")

    assert response.status_code == 200
    entry = response.json()["conversations"][0]
    assert entry["apiKeyId"] is None
    assert entry["apiKeyName"] is None
    _assert_no_seeded_sensitive_values(response, (stale_id, "stale-only-secret"))


@pytest.mark.asyncio
async def test_conversation_list_nullable_key_fields(async_client, db_setup):
    base = utcnow().replace(microsecond=0)
    async with SessionLocal() as session:
        logs = RequestLogsRepository(session)
        await logs.add_log(
            account_id=None,
            request_id="no-key",
            model="model",
            input_tokens=1,
            output_tokens=1,
            latency_ms=1,
            status="success",
            error_code=None,
            requested_at=base,
            conversation_id="no-key-conv",
        )

    response = await async_client.get("/api/conversations")
    assert response.status_code == 200
    entry = response.json()["conversations"][0]
    assert entry["apiKeyId"] is None
    assert entry["apiKeyName"] is None
    assert entry["representativeAccount"] is None
    assert entry["remainingAccountCount"] == 0


@pytest.mark.asyncio
async def test_conversation_details_exact_rows_elapsed_order_and_404(async_client, db_setup):
    base = utcnow().replace(microsecond=0)
    await _seed_conversations(base)

    response = await async_client.get("/api/conversations/Conv-A?sort=totalCostUsd")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "conversationId",
        "start",
        "latest",
        "accountCount",
        "totalElapsedTime",
        "dominantUseragentGroup",
        "modelStats",
    }
    assert body["conversationId"] == "Conv-A"
    assert body["accountCount"] == 2
    assert body["totalElapsedTime"] == 150
    assert body["dominantUseragentGroup"] == "Other"
    assert [row["modelEffort"] for row in body["modelStats"]] == [
        {"model": "model-b", "reasoningEffort": None},
        {"model": "model-a", "reasoningEffort": "low"},
        {"model": "model-a", "reasoningEffort": "high"},
    ]
    assert all(
        set(row)
        == {
            "modelEffort",
            "reqs",
            "totalElapsedTime",
            "totalInputTokens",
            "cachedInputTokens",
            "totalOutputTokens",
            "totalCostUsd",
        }
        for row in body["modelStats"]
    )
    assert body["modelStats"][0]["totalElapsedTime"] == 0
    assert body["modelStats"][1]["totalOutputTokens"] == 20

    blank = await async_client.get("/api/conversations/%20")
    unknown = await async_client.get("/api/conversations/unknown")
    assert blank.status_code == 404
    assert unknown.status_code == 404
    assert blank.json() == {"error": {"code": "http_404", "message": "Conversation not found"}}
    assert unknown.json() == {"error": {"code": "http_404", "message": "Conversation not found"}}


@pytest.mark.asyncio
async def test_conversation_cached_tokens_remain_null_when_all_values_are_unknown(async_client, db_setup):
    base = utcnow().replace(microsecond=0)
    async with SessionLocal() as session:
        logs = RequestLogsRepository(session)
        await logs.add_log(
            account_id=None,
            request_id="unknown-cache",
            model="unknown-cache-model",
            input_tokens=10,
            output_tokens=5,
            latency_ms=1,
            status="success",
            error_code=None,
            requested_at=base,
            conversation_id="unknown-cache-conversation",
            cached_input_tokens=None,
        )

    listing = await async_client.get("/api/conversations")
    assert listing.status_code == 200
    assert listing.json()["conversations"][0]["cachedInputTokens"] is None

    details = await async_client.get("/api/conversations/unknown-cache-conversation")
    assert details.status_code == 200
    assert details.json()["modelStats"][0]["cachedInputTokens"] is None


@pytest.mark.asyncio
async def test_conversation_details_accepts_slash_containing_ids(async_client, db_setup):
    base = utcnow().replace(microsecond=0)
    conversation_id = "workspace/thread-1"
    async with SessionLocal() as session:
        logs = RequestLogsRepository(session)
        await logs.add_log(
            account_id=None,
            request_id="slash-conversation",
            model="slash-model",
            input_tokens=10,
            output_tokens=5,
            latency_ms=1,
            status="success",
            error_code=None,
            requested_at=base,
            conversation_id=conversation_id,
        )

    listing = await async_client.get("/api/conversations")
    assert listing.status_code == 200
    assert listing.json()["conversations"][0]["conversationId"] == conversation_id

    details = await async_client.get(f"/api/conversations/{quote(conversation_id, safe='')}")
    assert details.status_code == 200
    assert details.json()["conversationId"] == conversation_id


@pytest.mark.parametrize("conversation_id", [".", ".."])
@pytest.mark.asyncio
async def test_conversation_details_accepts_dot_only_ids(async_client, db_setup, conversation_id):
    base = utcnow().replace(microsecond=0)
    async with SessionLocal() as session:
        logs = RequestLogsRepository(session)
        await logs.add_log(
            account_id=None,
            request_id=f"dot-conversation-{conversation_id}",
            model="dot-model",
            input_tokens=10,
            output_tokens=5,
            latency_ms=1,
            status="success",
            error_code=None,
            requested_at=base,
            conversation_id=conversation_id,
        )

    details = await async_client.get(f"/api/conversations/{quote(f' {conversation_id}', safe='')}")

    assert details.status_code == 200
    assert details.json()["conversationId"] == conversation_id


@pytest.mark.asyncio
async def test_conversation_identity_preserves_non_sql_whitespace_for_list_and_detail(async_client, db_setup):
    base = utcnow().replace(microsecond=0)
    nbsp_identity = "\u00a0nbsp-conversation\u00a0"
    async with SessionLocal() as session:
        logs = RequestLogsRepository(session)
        for request_id, conversation_id in (
            ("nbsp-identity", nbsp_identity),
            ("ascii-padded-identity", "  ascii-padded  "),
            ("ascii-blank-identity", "   "),
        ):
            await logs.add_log(
                account_id=None,
                request_id=request_id,
                model="model",
                input_tokens=1,
                output_tokens=1,
                latency_ms=1,
                status="success",
                error_code=None,
                requested_at=base,
                conversation_id=conversation_id,
            )

    response = await async_client.get("/api/conversations")

    assert response.status_code == 200
    identities = [row["conversationId"] for row in response.json()["conversations"]]
    assert nbsp_identity in identities
    assert "ascii-padded" in identities
    assert "ascii-blank-identity" not in identities

    detail = await async_client.get(f"/api/conversations/{quote(nbsp_identity, safe='')}")

    assert detail.status_code == 200
    assert detail.json()["conversationId"] == nbsp_identity


@pytest.mark.asyncio
async def test_conversation_details_excludes_warmups_and_soft_deleted_rows(async_client, db_setup):
    base = utcnow().replace(microsecond=0)
    async with SessionLocal() as session:
        accounts = AccountsRepository(session)
        logs = RequestLogsRepository(session)
        await accounts.upsert(_account("eligible-account"))
        normal_rows = (
            ("eligible-1", base - timedelta(minutes=2), 10, 5, 1.0),
            ("eligible-2", base - timedelta(minutes=1), 20, 10, 2.0),
        )
        for request_id, requested_at, input_tokens, output_tokens, cost_usd in normal_rows:
            await logs.add_log(
                account_id="eligible-account",
                request_id=request_id,
                model="eligible-model",
                reasoning_effort="high",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=input_tokens,
                cost_usd=cost_usd,
                status="success",
                error_code=None,
                requested_at=requested_at,
                conversation_id="eligible-conversation",
            )
        for request_id, request_kind in (
            ("excluded-warmup", RequestKind.WARMUP.value),
            ("excluded-limit-warmup", "limit_warmup"),
        ):
            await logs.add_log(
                account_id=None,
                request_id=request_id,
                model="excluded-model",
                reasoning_effort="excluded",
                input_tokens=999,
                output_tokens=999,
                latency_ms=999,
                cost_usd=999.0,
                status="success",
                error_code=None,
                requested_at=base + timedelta(minutes=1),
                conversation_id="eligible-conversation",
                request_kind=request_kind,
            )
        deleted = await logs.add_log(
            account_id=None,
            request_id="excluded-deleted",
            model="deleted-model",
            reasoning_effort="deleted",
            input_tokens=999,
            output_tokens=999,
            latency_ms=999,
            cost_usd=999.0,
            status="success",
            error_code=None,
            requested_at=base + timedelta(minutes=2),
            conversation_id="eligible-conversation",
        )
        await session.execute(update(RequestLog).where(RequestLog.id == deleted.id).values(deleted_at=base))
        await session.commit()

    response = await async_client.get("/api/conversations/eligible-conversation")

    assert response.status_code == 200
    body = response.json()
    assert body["accountCount"] == 1
    assert body["totalElapsedTime"] == 30
    assert body["start"].startswith((base - timedelta(minutes=2)).isoformat())
    assert body["latest"].startswith((base - timedelta(minutes=1)).isoformat())
    assert body["modelStats"] == [
        {
            "modelEffort": {"model": "eligible-model", "reasoningEffort": "high"},
            "reqs": 2,
            "totalElapsedTime": 30,
            "totalInputTokens": 30,
            "cachedInputTokens": None,
            "totalOutputTokens": 15,
            "totalCostUsd": pytest.approx(3.0),
        }
    ]


@pytest.mark.asyncio
async def test_conversation_details_reasoning_effort_tie_uses_explicit_rank(async_client, db_setup):
    base = utcnow().replace(microsecond=0)
    async with SessionLocal() as session:
        logs = RequestLogsRepository(session)
        for request_id, reasoning_effort in (
            ("effort-none", None),
            ("effort-empty", ""),
            ("effort-nonempty", "high"),
        ):
            await logs.add_log(
                account_id=None,
                request_id=request_id,
                model="same-model",
                reasoning_effort=reasoning_effort,
                input_tokens=1,
                output_tokens=1,
                latency_ms=1,
                cost_usd=0.1,
                status="success",
                error_code=None,
                requested_at=base,
                conversation_id="effort-tie-conversation",
            )

    response = await async_client.get("/api/conversations/effort-tie-conversation")

    assert response.status_code == 200
    assert [row["modelEffort"] for row in response.json()["modelStats"]] == [
        {"model": "same-model", "reasoningEffort": None},
        {"model": "same-model", "reasoningEffort": ""},
        {"model": "same-model", "reasoningEffort": "high"},
    ]


@pytest.mark.asyncio
async def test_since_filter_includes_conversation_active_in_window(async_client, db_setup):
    base = utcnow().replace(microsecond=0)
    await _seed_conversation_rows(
        [
            _ConversationSeed(
                request_id="since-old-1",
                account_id="since-old-account-a",
                model="since-old-model-a",
                reasoning_effort=None,
                input_tokens=100,
                output_tokens=10,
                cached_input_tokens=None,
                reasoning_tokens=0,
                latency_ms=10,
                cost_usd=1.0,
                requested_at=base - timedelta(days=10),
                conversation_id="conv-old",
            ),
            _ConversationSeed(
                request_id="since-old-0",
                account_id="since-old-account-a",
                model="since-old-model-a",
                reasoning_effort=None,
                input_tokens=50,
                output_tokens=5,
                cached_input_tokens=None,
                reasoning_tokens=0,
                latency_ms=5,
                cost_usd=0.5,
                requested_at=base - timedelta(days=11),
                conversation_id="conv-old",
            ),
            _ConversationSeed(
                request_id="since-old-2",
                account_id="since-old-account-b",
                model="since-old-model-b",
                reasoning_effort=None,
                input_tokens=200,
                output_tokens=20,
                cached_input_tokens=None,
                reasoning_tokens=0,
                latency_ms=20,
                cost_usd=2.0,
                requested_at=base - timedelta(days=1),
                conversation_id="conv-old",
            ),
            _ConversationSeed(
                request_id="since-new-1",
                account_id="since-new-account-a",
                model="since-new-model-a",
                reasoning_effort=None,
                input_tokens=30,
                output_tokens=7,
                cached_input_tokens=None,
                reasoning_tokens=0,
                latency_ms=30,
                cost_usd=0.3,
                requested_at=base - timedelta(days=1),
                conversation_id="conv-new",
            ),
            _ConversationSeed(
                request_id="since-new-2",
                account_id="since-new-account-b",
                model="since-new-model-b",
                reasoning_effort=None,
                input_tokens=40,
                output_tokens=8,
                cached_input_tokens=None,
                reasoning_tokens=0,
                latency_ms=40,
                cost_usd=0.4,
                requested_at=base - timedelta(hours=12),
                conversation_id="conv-new",
            ),
        ]
    )

    since = base - timedelta(days=7)
    response = await async_client.get(f"/api/conversations?since={since.isoformat()}")

    assert response.status_code == 200
    body = response.json()
    assert [row["conversationId"] for row in body["conversations"]] == ["conv-new", "conv-old"]
    assert body["total"] == 2
    entry = body["conversations"][1]
    assert entry["requestCount"] == 3
    assert entry["representativeAccount"] == "since-old-account-a"
    assert entry["remainingAccountCount"] == 1
    assert entry["representativeModel"] == "since-old-model-a"
    assert entry["remainingModelCount"] == 1
    assert entry["totalTokens"] == 385
    assert entry["totalCostUsd"] == pytest.approx(3.5)
    assert entry["firstRequest"].startswith((base - timedelta(days=11)).isoformat())


@pytest.mark.asyncio
async def test_since_filter_preserves_true_earliest_conversation_start(async_client, db_setup):
    base = utcnow().replace(microsecond=0)
    await _seed_conversation_rows(
        [
            _ConversationSeed(
                request_id="started-before-window",
                account_id="started-before-window-account",
                model="started-before-window-model",
                reasoning_effort=None,
                input_tokens=10,
                output_tokens=1,
                cached_input_tokens=None,
                reasoning_tokens=0,
                latency_ms=10,
                cost_usd=0.1,
                requested_at=base - timedelta(days=60),
                conversation_id="conv-long-running",
            ),
            _ConversationSeed(
                request_id="active-in-window",
                account_id="active-in-window-account",
                model="active-in-window-model",
                reasoning_effort=None,
                input_tokens=20,
                output_tokens=2,
                cached_input_tokens=None,
                reasoning_tokens=0,
                latency_ms=20,
                cost_usd=0.2,
                requested_at=base - timedelta(days=1),
                conversation_id="conv-long-running",
            ),
        ]
    )

    since = base - timedelta(days=30)
    response = await async_client.get(f"/api/conversations?since={since.isoformat()}")

    assert response.status_code == 200
    entries = response.json()["conversations"]
    assert len(entries) == 1
    assert entries[0]["conversationId"] == "conv-long-running"
    assert datetime.fromisoformat(entries[0]["firstRequest"]).replace(tzinfo=None) < since.replace(tzinfo=None)


@pytest.mark.asyncio
async def test_conversation_list_bare_request_is_bounded_to_recent_30_days(async_client, db_setup):
    base = utcnow().replace(microsecond=0)
    await _seed_conversation_rows(
        [
            _ConversationSeed(
                request_id="bare-old",
                account_id="bare-old-account",
                model="bare-old-model",
                reasoning_effort=None,
                input_tokens=10,
                output_tokens=1,
                cached_input_tokens=None,
                reasoning_tokens=0,
                latency_ms=10,
                cost_usd=0.1,
                requested_at=base - timedelta(days=31),
                conversation_id="bare-old-conversation",
            ),
            _ConversationSeed(
                request_id="bare-recent",
                account_id="bare-recent-account",
                model="bare-recent-model",
                reasoning_effort=None,
                input_tokens=20,
                output_tokens=2,
                cached_input_tokens=None,
                reasoning_tokens=0,
                latency_ms=20,
                cost_usd=0.2,
                requested_at=base - timedelta(days=1),
                conversation_id="bare-recent-conversation",
            ),
        ]
    )

    response = await async_client.get("/api/conversations")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert [row["conversationId"] for row in body["conversations"]] == ["bare-recent-conversation"]

    ancient = await async_client.get(
        "/api/conversations",
        params={"since": (base - timedelta(days=365)).isoformat()},
    )
    assert ancient.status_code == 200
    assert ancient.json()["total"] == 1
    assert [row["conversationId"] for row in ancient.json()["conversations"]] == ["bare-recent-conversation"]


@pytest.mark.asyncio
async def test_since_filter_composes_with_search(async_client, db_setup):
    base = utcnow().replace(microsecond=0)
    await _seed_conversation_rows(
        [
            _ConversationSeed(
                request_id="since-search-match",
                account_id="since-search-account-a",
                model="since-search-model-a",
                reasoning_effort=None,
                input_tokens=10,
                output_tokens=5,
                cached_input_tokens=None,
                reasoning_tokens=0,
                latency_ms=10,
                cost_usd=0.1,
                requested_at=base - timedelta(days=2),
                conversation_id="conv-search-match",
                useragent_group="known-since-search-term",
            ),
            _ConversationSeed(
                request_id="since-search-other",
                account_id="since-search-account-b",
                model="since-search-model-b",
                reasoning_effort=None,
                input_tokens=20,
                output_tokens=5,
                cached_input_tokens=None,
                reasoning_tokens=0,
                latency_ms=20,
                cost_usd=0.2,
                requested_at=base - timedelta(days=1),
                conversation_id="conv-search-other",
                useragent_group="unrelated-agent",
            ),
        ]
    )

    since = base - timedelta(days=7)
    response = await async_client.get(
        "/api/conversations",
        params={"since": since.isoformat(), "search": "known-since-search-term"},
    )

    assert response.status_code == 200
    body = response.json()
    assert [row["conversationId"] for row in body["conversations"]] == ["conv-search-match"]
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_conversation_membership_candidates_are_bounded_before_full_history_aggregation(db_setup):
    base = utcnow().replace(microsecond=0)
    since = base - timedelta(days=7)
    await _seed_conversation_rows(
        [
            _ConversationSeed(
                request_id="bounded-active-old",
                account_id="bounded-active-account",
                model="bounded-active-model",
                reasoning_effort=None,
                input_tokens=10,
                output_tokens=1,
                cached_input_tokens=None,
                reasoning_tokens=0,
                latency_ms=10,
                cost_usd=1.0,
                requested_at=base - timedelta(days=60),
                conversation_id="bounded-active",
            ),
            _ConversationSeed(
                request_id="bounded-active-recent",
                account_id="bounded-active-account",
                model="bounded-active-model",
                reasoning_effort=None,
                input_tokens=20,
                output_tokens=2,
                cached_input_tokens=None,
                reasoning_tokens=0,
                latency_ms=20,
                cost_usd=2.0,
                requested_at=base - timedelta(days=1),
                conversation_id="bounded-active",
            ),
            _ConversationSeed(
                request_id="bounded-inactive-old",
                account_id="bounded-inactive-account",
                model="bounded-inactive-model",
                reasoning_effort=None,
                input_tokens=30,
                output_tokens=3,
                cached_input_tokens=None,
                reasoning_tokens=0,
                latency_ms=30,
                cost_usd=3.0,
                requested_at=base - timedelta(days=60),
                conversation_id="bounded-inactive",
            ),
        ]
    )

    statements: list[str] = []

    def _capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _capture)
    try:
        async with SessionLocal() as session:
            result = await RequestLogsRepository(session).list_conversations(since=since)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _capture)

    assert result.total == 1
    assert [summary.conversation_id for summary in result.summaries] == ["bounded-active"]
    assert result.summaries[0].request_count == 2
    assert result.summaries[0].cost_usd == pytest.approx(3.0)

    candidate_fragments = _distinct_candidate_fragments(statements)
    assert candidate_fragments
    assert any("REQUEST_LOGS.REQUESTED_AT >= " in fragment for fragment in candidate_fragments)
    emitted_sql = "\n".join(statements).lower()
    assert "having count" not in emitted_sql
    assert "conversation_id in (select" in emitted_sql


@pytest.mark.asyncio
async def test_conversation_search_candidates_are_bounded_to_activity_window(db_setup):
    base = utcnow().replace(microsecond=0)
    since = base - timedelta(days=7)
    await _seed_conversation_rows(
        [
            _ConversationSeed(
                request_id="bounded-search-old",
                account_id="bounded-search-account",
                model="bounded-search-model",
                reasoning_effort=None,
                input_tokens=10,
                output_tokens=1,
                cached_input_tokens=None,
                reasoning_tokens=0,
                latency_ms=10,
                cost_usd=1.0,
                requested_at=base - timedelta(days=60),
                conversation_id="bounded-search",
                useragent_group="other-agent",
            ),
            _ConversationSeed(
                request_id="bounded-search-recent",
                account_id="bounded-search-account",
                model="bounded-search-model",
                reasoning_effort=None,
                input_tokens=20,
                output_tokens=2,
                cached_input_tokens=None,
                reasoning_tokens=0,
                latency_ms=20,
                cost_usd=2.0,
                requested_at=base - timedelta(days=1),
                conversation_id="bounded-search",
                useragent_group="needle-agent",
            ),
            _ConversationSeed(
                request_id="bounded-search-old-match",
                account_id="bounded-search-old-match-account",
                model="bounded-search-old-match-model",
                reasoning_effort=None,
                input_tokens=30,
                output_tokens=3,
                cached_input_tokens=None,
                reasoning_tokens=0,
                latency_ms=30,
                cost_usd=3.0,
                requested_at=base - timedelta(days=60),
                conversation_id="bounded-search-old-match",
                useragent_group="needle-agent",
            ),
            _ConversationSeed(
                request_id="bounded-search-old-match-recent",
                account_id="bounded-search-old-match-account",
                model="bounded-search-old-match-model",
                reasoning_effort=None,
                input_tokens=40,
                output_tokens=4,
                cached_input_tokens=None,
                reasoning_tokens=0,
                latency_ms=40,
                cost_usd=4.0,
                requested_at=base - timedelta(days=1),
                conversation_id="bounded-search-old-match",
                useragent_group="other-agent",
            ),
        ]
    )

    statements: list[str] = []

    def _capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _capture)
    try:
        async with SessionLocal() as session:
            result = await RequestLogsRepository(session).list_conversations(since=since, search="needle")
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _capture)

    assert result.total == 1
    assert result.summaries[0].conversation_id == "bounded-search"
    assert result.summaries[0].request_count == 2
    candidate_fragments = _distinct_candidate_fragments(statements)
    assert candidate_fragments
    assert any("REQUEST_LOGS.REQUESTED_AT >= " in fragment for fragment in candidate_fragments)
    assert any("REQUEST_LOGS.USERAGENT_GROUP" in fragment for fragment in candidate_fragments)


@pytest.mark.asyncio
async def test_conversation_listing_total_cached_across_identical_signatures(async_client, db_setup, monkeypatch):
    from app.modules.request_logs import repository as logs_repository_module

    monkeypatch.setattr(logs_repository_module, "_COUNT_CACHE_TTL_SECONDS", 30.0)
    logs_repository_module._clear_recent_count_cache()

    base = utcnow().replace(microsecond=0)
    await _seed_conversation_rows(
        [
            _ConversationSeed(
                request_id="cache-old",
                account_id="cache-account-old",
                model="cache-model-old",
                reasoning_effort=None,
                input_tokens=10,
                output_tokens=1,
                cached_input_tokens=None,
                reasoning_tokens=0,
                latency_ms=10,
                cost_usd=0.1,
                requested_at=base - timedelta(days=6),
                conversation_id="cache-old-conversation",
            ),
            _ConversationSeed(
                request_id="cache-new",
                account_id="cache-account-new",
                model="cache-model-new",
                reasoning_effort=None,
                input_tokens=20,
                output_tokens=2,
                cached_input_tokens=None,
                reasoning_tokens=0,
                latency_ms=20,
                cost_usd=0.2,
                requested_at=base - timedelta(hours=12),
                conversation_id="cache-new-conversation",
            ),
        ]
    )

    wide_since = base - timedelta(days=7)
    wide_url = f"/api/conversations?since={wide_since.isoformat()}"
    first = await async_client.get(wide_url)
    second = await async_client.get(wide_url)
    assert first.status_code == 200
    assert second.status_code == 200
    t1 = first.json()["total"]
    t2 = second.json()["total"]
    assert t1 == 2
    assert t2 == t1
    assert ("conversation-count", None, ("since", wide_since)) in logs_repository_module._recent_count_cache

    logs_repository_module._clear_recent_count_cache()

    narrow_since = base - timedelta(days=1)
    narrow = await async_client.get(f"/api/conversations?since={narrow_since.isoformat()}")
    assert narrow.status_code == 200
    assert narrow.json()["total"] == 1
    assert ("conversation-count", None, ("since", narrow_since)) in logs_repository_module._recent_count_cache

    matching_old = await async_client.get(
        "/api/conversations",
        params={"since": wide_since.isoformat(), "search": "cache-old"},
    )
    matching_new = await async_client.get(
        "/api/conversations",
        params={"since": wide_since.isoformat(), "search": "cache-new"},
    )
    assert matching_old.status_code == 200
    assert matching_new.status_code == 200
    assert matching_old.json()["total"] == 1
    assert matching_new.json()["total"] == 1
    assert ("conversation-count", "cache-old", ("since", wide_since)) in logs_repository_module._recent_count_cache
    assert ("conversation-count", "cache-new", ("since", wide_since)) in logs_repository_module._recent_count_cache

    logs_repository_module._clear_recent_count_cache()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("timeframe", "expected_ids"),
    [
        ("1d", ["conv-one-day"]),
        ("7d", ["conv-one-day", "conv-seven-days"]),
        ("30d", ["conv-one-day", "conv-seven-days", "conv-thirty-days"]),
    ],
)
async def test_conversation_timeframe_uses_server_window(
    async_client,
    db_setup,
    monkeypatch: pytest.MonkeyPatch,
    timeframe: str,
    expected_ids: list[str],
):
    fixed_now = datetime(2026, 1, 31, 12, 0, 0)
    monkeypatch.setattr("app.modules.dashboard.timeframes.utcnow", lambda: fixed_now)
    monkeypatch.setattr("app.modules.request_logs.api.utcnow", lambda: fixed_now)

    async with SessionLocal() as session:
        session.add_all(
            [
                RequestLog(
                    account_id=None,
                    request_id="timeframe-one-day",
                    requested_at=fixed_now - timedelta(hours=12),
                    model="model",
                    status="success",
                    conversation_id="conv-one-day",
                ),
                RequestLog(
                    account_id=None,
                    request_id="timeframe-seven-days",
                    requested_at=fixed_now - timedelta(days=3),
                    model="model",
                    status="success",
                    conversation_id="conv-seven-days",
                ),
                RequestLog(
                    account_id=None,
                    request_id="timeframe-thirty-days",
                    requested_at=fixed_now - timedelta(days=14),
                    model="model",
                    status="success",
                    conversation_id="conv-thirty-days",
                ),
                RequestLog(
                    account_id=None,
                    request_id="timeframe-outside",
                    requested_at=fixed_now - timedelta(days=31),
                    model="model",
                    status="success",
                    conversation_id="conv-outside",
                ),
            ]
        )
        await session.commit()

    response = await async_client.get("/api/conversations", params={"timeframe": timeframe})

    assert response.status_code == 200
    assert [row["conversationId"] for row in response.json()["conversations"]] == expected_ids


@pytest.mark.asyncio
async def test_conversation_timeframe_matches_dashboard_activity_under_fixed_clock(
    async_client,
    db_setup,
    monkeypatch: pytest.MonkeyPatch,
):
    fixed_now = datetime(2026, 1, 31, 12, 0, 0)
    monkeypatch.setattr("app.modules.dashboard.service.utcnow", lambda: fixed_now)
    monkeypatch.setattr("app.modules.dashboard.timeframes.utcnow", lambda: fixed_now)
    monkeypatch.setattr("app.modules.request_logs.api.utcnow", lambda: fixed_now)

    async with SessionLocal() as session:
        session.add_all(
            [
                RequestLog(
                    account_id=None,
                    request_id="parity-inside",
                    requested_at=fixed_now - timedelta(days=1),
                    model="model",
                    status="success",
                    conversation_id="conv-inside",
                ),
                RequestLog(
                    account_id=None,
                    request_id="parity-outside",
                    requested_at=fixed_now - timedelta(days=8),
                    model="model",
                    status="success",
                    conversation_id="conv-outside",
                ),
                RequestLog(
                    account_id=None,
                    request_id="parity-long-outside",
                    requested_at=fixed_now - timedelta(days=8),
                    model="model",
                    status="success",
                    conversation_id="conv-long",
                ),
                RequestLog(
                    account_id=None,
                    request_id="parity-long-inside",
                    requested_at=fixed_now - timedelta(hours=1),
                    model="model",
                    status="success",
                    conversation_id="conv-long",
                ),
            ]
        )
        await session.commit()

    conversations_response = await async_client.get("/api/conversations", params={"timeframe": "7d"})
    dashboard_response = await async_client.get("/api/dashboard/overview", params={"timeframe": "7d"})

    assert conversations_response.status_code == 200
    assert dashboard_response.status_code == 200
    conversations = conversations_response.json()
    dashboard = dashboard_response.json()
    conversation_ids = {row["conversationId"] for row in conversations["conversations"]}
    assert conversation_ids == {"conv-inside", "conv-long"}
    assert conversations["total"] == 2
    assert dashboard["summary"]["metrics"]["conversations"] == len(conversation_ids)
    assert dashboard["summary"]["metrics"]["conversationRequests"] == 2


@pytest.mark.asyncio
async def test_conversation_unknown_timeframe_returns_validation_error(async_client):
    response = await async_client.get("/api/conversations", params={"timeframe": "24h"})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_conversation_timeframe_and_since_are_mutually_exclusive(async_client):
    response = await async_client.get(
        "/api/conversations",
        params={"timeframe": "7d", "since": "2025-01-01T00:00:00Z"},
    )

    assert response.status_code == 422
    assert "timeframe and since cannot be supplied together" in response.text


@pytest.mark.asyncio
async def test_conversation_since_timezone_offset_is_normalized_to_utc(
    async_client,
    db_setup,
    monkeypatch: pytest.MonkeyPatch,
):
    fixed_now = datetime(2025, 1, 10, 12, 0, 0)
    monkeypatch.setattr("app.modules.request_logs.api.utcnow", lambda: fixed_now)
    async with SessionLocal() as session:
        session.add_all(
            [
                RequestLog(
                    account_id=None,
                    request_id="offset-inside",
                    requested_at=datetime(2025, 1, 1, 0, 30, 0),
                    model="model",
                    status="success",
                    conversation_id="conv-offset-inside",
                ),
                RequestLog(
                    account_id=None,
                    request_id="offset-before",
                    requested_at=datetime(2024, 12, 31, 23, 30, 0),
                    model="model",
                    status="success",
                    conversation_id="conv-offset-before",
                ),
            ]
        )
        await session.commit()

    response = await async_client.get(
        "/api/conversations",
        params={"since": "2025-01-01T02:00:00+02:00"},
    )

    assert response.status_code == 200
    assert [row["conversationId"] for row in response.json()["conversations"]] == ["conv-offset-inside"]


@pytest.mark.asyncio
async def test_conversation_since_older_than_30_days_is_clamped(
    async_client,
    db_setup,
    monkeypatch: pytest.MonkeyPatch,
):
    fixed_now = datetime(2025, 2, 10, 12, 0, 0)
    monkeypatch.setattr("app.modules.request_logs.api.utcnow", lambda: fixed_now)
    async with SessionLocal() as session:
        session.add_all(
            [
                RequestLog(
                    account_id=None,
                    request_id="clamp-day-35",
                    requested_at=fixed_now - timedelta(days=35),
                    model="model",
                    status="success",
                    conversation_id="conv-day-35",
                ),
                RequestLog(
                    account_id=None,
                    request_id="clamp-day-20",
                    requested_at=fixed_now - timedelta(days=20),
                    model="model",
                    status="success",
                    conversation_id="conv-day-20",
                ),
            ]
        )
        await session.commit()

    response = await async_client.get(
        "/api/conversations",
        params={"since": (fixed_now - timedelta(days=90)).isoformat()},
    )

    assert response.status_code == 200
    assert [row["conversationId"] for row in response.json()["conversations"]] == ["conv-day-20"]


@pytest.mark.asyncio
async def test_conversation_routes_reject_guest_principal(app_instance, async_client, monkeypatch):
    original_validate_dashboard_session = auth_dependencies.validate_dashboard_session
    app_instance.dependency_overrides[original_validate_dashboard_session] = guest_principal
    monkeypatch.setattr(
        auth_dependencies,
        "validate_dashboard_session",
        AsyncMock(return_value=guest_principal()),
    )
    try:
        for path in (
            "/api/conversations",
            "/api/conversations/",
            "/api/conversations/guest-hidden",
        ):
            response = await async_client.get(path)
            assert response.status_code == 403
            payload = response.json()
            assert payload["error"]["code"] == "admin_access_required"
            assert "conversations" not in payload
            assert "conversationId" not in payload
    finally:
        app_instance.dependency_overrides.pop(original_validate_dashboard_session, None)


@pytest.mark.asyncio
async def test_conversation_since_future_timestamp_returns_empty_result(
    async_client,
    db_setup,
    monkeypatch: pytest.MonkeyPatch,
):
    fixed_now = datetime(2025, 2, 10, 12, 0, 0)
    monkeypatch.setattr("app.modules.request_logs.api.utcnow", lambda: fixed_now)
    async with SessionLocal() as session:
        session.add(
            RequestLog(
                account_id=None,
                request_id="future-existing-row",
                requested_at=fixed_now - timedelta(hours=1),
                model="model",
                status="success",
                conversation_id="conv-existing",
            )
        )
        await session.commit()

    response = await async_client.get(
        "/api/conversations",
        params={"since": (fixed_now + timedelta(days=1)).isoformat()},
    )

    assert response.status_code == 200
    assert response.json() == {"conversations": [], "total": 0, "hasMore": False}
