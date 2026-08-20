from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import get_args

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.balancer.logic import RoutingStrategy
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, ApiKey, Base, ModelSource, RequestLog
from app.modules.telemetry.clients import (
    CANONICAL_CLIENT_FAMILIES,
    CLIENT_FAMILY_BY_RAW_GROUP,
    ClientCount,
    client_family,
    client_shares,
)
from app.modules.telemetry.schemas import (
    TelemetryActivation,
    TelemetryOptOut,
    TelemetryRegistration,
    build_snapshot_envelope,
)
from app.modules.telemetry.snapshot import (
    _ROUTING_POLICIES,
    TelemetrySnapshotBuilder,
    _canonical_routing_policy,
    cost_bucket,
    count_bucket,
    db_size_bucket,
    output_tokens_bucket,
)

pytestmark = pytest.mark.unit


@pytest.fixture
async def async_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _request_log(
    request_id: str,
    *,
    model: str,
    useragent_group: str,
    reasoning_effort: str | None = None,
    output_tokens: int = 100,
    account_id: str | None = None,
    status: str = "success",
    **values,
) -> RequestLog:
    return RequestLog(
        account_id=account_id,
        request_id=request_id,
        requested_at=utcnow(),
        model=model,
        status=status,
        useragent_group=useragent_group,
        reasoning_effort=reasoning_effort,
        input_tokens=200,
        output_tokens=output_tokens,
        cached_input_tokens=50,
        cost_usd=1.0,
        latency_ms=1_000,
        latency_first_token_ms=400,
        transport="http",
        **values,
    )


@pytest.mark.asyncio
async def test_snapshot_serialized_field_set_matches_documented_schema(async_session: AsyncSession) -> None:
    async_session.add(_request_log("schema", model="gpt-5.4", useragent_group="codex_exec"))
    await async_session.commit()

    snapshot = await TelemetrySnapshotBuilder(async_session).build(
        "00000000-0000-4000-8000-000000000001",
        consent="undecided",
    )
    payload = snapshot.model_dump()

    assert payload["consent"] == "undecided"
    assert set(payload) == {
        "schema_version",
        "consent",
        "instance_id",
        "version",
        "python",
        "os",
        "arch",
        "uptime_hours",
        "deploy",
        "accounts",
        "usage_7d",
        "features",
    }
    assert set(payload["deploy"]) == {"method", "db_backend", "db_size_bucket", "replicas", "reverse_proxy"}
    assert set(payload["accounts"]) == {
        "pool_bucket",
        "plan_mix",
        "workspace_accounts",
        "routing_policy",
        "limit_warmup_enabled",
        "egress_proxy_used",
    }
    assert set(payload["accounts"]["plan_mix"]) == {"plus", "pro", "team", "free"}
    assert set(payload["usage_7d"]) == {
        "requests",
        "success_rate",
        "tokens_input",
        "tokens_output",
        "tokens_cached_ratio",
        "cost_usd_bucket",
        "request_kinds",
        "transport_mix",
        "service_tier_mix",
        "clients",
        "clients_other_ratio",
        "models",
        "latency_ms_p50",
        "ttft_ms_p50",
        "ttft_ms_p95",
        "rate_limit_429_ratio",
        "top_upstream_errors",
    }
    assert set(payload["usage_7d"]["request_kinds"]) == {"responses", "chat", "images", "unknown"}
    assert set(payload["usage_7d"]["transport_mix"]) == {"ws", "http_bridge"}
    assert set(payload["usage_7d"]["service_tier_mix"]) == {"default", "flex", "priority"}
    assert set(payload["usage_7d"]["models"][0]) == {
        "name",
        "share",
        "reasoning",
        "avg_output_tokens_bucket",
    }
    assert set(payload["features"]) == {
        "api_firewall",
        "quota_planner",
        "sticky_sessions",
        "conversation_archive",
        "automations",
        "fleet",
        "model_sources_count",
        "api_keys_bucket",
        "prometheus",
        "otel",
        "dashboard_auth",
        "reset_credits",
        "image_api_used",
    }

    registration = TelemetryRegistration(
        app_version=snapshot.version,
        deployment_mode=snapshot.deploy.method,
        instance_id=snapshot.instance_id,
        os_arch=f"{snapshot.os}/{snapshot.arch}",
        public_key="00",
    ).model_dump(mode="json")
    activation = TelemetryActivation().model_dump(mode="json")
    opt_out = TelemetryOptOut(
        app_version=snapshot.version,
        instance_id=snapshot.instance_id,
        occurred_at="2026-08-20T12:00:00Z",
    ).model_dump(mode="json")
    envelope = build_snapshot_envelope(snapshot).model_dump(mode="json")
    assert set(registration) == {
        "app_name",
        "app_version",
        "deployment_mode",
        "environment",
        "instance_id",
        "os_arch",
        "public_key",
    }
    assert set(activation) == {"action"}
    assert set(opt_out) == {"app_version", "event", "instance_id", "occurred_at"}
    assert set(envelope) == {"instance_id", "metrics", "timestamp"}


def test_client_mapping_table_and_unknown_family_are_allowlisted() -> None:
    for raw_group, expected_family in CLIENT_FAMILY_BY_RAW_GROUP.items():
        assert client_family(raw_group) == expected_family
    assert client_family("senpi") == "other"

    shares, other_ratio = client_shares(
        [
            ClientCount("codex_exec", 2),
            ClientCount("codex-tui", 3),
            ClientCount("senpi", 1),
        ]
    )
    assert shares == {"codex-cli": 0.833333, "other": 0.166667}
    assert other_ratio == 0.166667
    assert "senpi" not in str(shares)


def test_client_share_emission_rejects_noncanonical_mapping(monkeypatch) -> None:
    monkeypatch.setitem(CLIENT_FAMILY_BY_RAW_GROUP, "unexpected", "private-client")
    assert "private-client" not in CANONICAL_CLIENT_FAMILIES

    with pytest.raises(ValueError, match="non-canonical telemetry client family"):
        client_shares([ClientCount("unexpected", 1)])


def test_routing_policy_allowlist_is_derived_from_balancer_declaration() -> None:
    assert _ROUTING_POLICIES == frozenset(get_args(RoutingStrategy))
    for strategy in get_args(RoutingStrategy):
        assert _canonical_routing_policy(strategy) == strategy


@pytest.mark.asyncio
async def test_model_catalog_filter_merges_custom_models_and_scopes_reasoning(
    async_session: AsyncSession,
) -> None:
    async_session.add_all(
        [
            _request_log("official-high", model="gpt-5.4", useragent_group="OpenAI", reasoning_effort="high"),
            _request_log("official-low", model="gpt-5.4", useragent_group="OpenAI", reasoning_effort="low"),
            _request_log(
                "private-high",
                model="corp-internal-gpt",
                useragent_group="senpi",
                reasoning_effort="high",
                output_tokens=2_000,
            ),
            _request_log(
                "private-custom-effort",
                model="another-private-model",
                useragent_group="senpi",
                reasoning_effort="secret-effort",
                output_tokens=2_000,
            ),
        ]
    )
    await async_session.commit()

    payload = (
        await TelemetrySnapshotBuilder(async_session).build(
            "00000000-0000-4000-8000-000000000002",
            consent="enabled",
        )
    ).model_dump()
    assert payload["consent"] == "enabled"
    models = {model["name"]: model for model in payload["usage_7d"]["models"]}

    assert set(models) == {"gpt-5.4", "other"}
    assert models["gpt-5.4"]["reasoning"] == {"high": 0.5, "low": 0.5}
    assert models["other"]["reasoning"] == {"high": 0.5, "other": 0.5}
    assert models["other"]["share"] == 0.5
    assert models["other"]["avg_output_tokens_bucket"] == "1k-4k"
    assert "reasoning" not in payload["usage_7d"]
    serialized = str(payload)
    assert "corp-internal-gpt" not in serialized
    assert "another-private-model" not in serialized
    assert "secret-effort" not in serialized


@pytest.mark.asyncio
async def test_request_kind_mix_fails_honest_without_persisted_route_family(async_session: AsyncSession) -> None:
    async_session.add_all(
        [
            _request_log("subscription", model="gpt-5.4", useragent_group="codex_exec"),
            _request_log(
                "source-backed",
                model="gpt-5.4",
                useragent_group="OpenAI",
                source="model_source",
            ),
            _request_log(
                "image-shaped",
                model="gpt-image-1",
                useragent_group="OpenAI",
                source="model_source",
            ),
        ]
    )
    await async_session.commit()

    payload = await TelemetrySnapshotBuilder(async_session).build(
        "00000000-0000-4000-8000-000000000005",
        consent="undecided",
    )

    assert payload.usage_7d.request_kinds.model_dump() == {
        "responses": 0.0,
        "chat": 0.0,
        "images": 0.0,
        "unknown": 1.0,
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0"),
        (1, "1"),
        (2, "2-5"),
        (5, "2-5"),
        (6, "6-20"),
        (20, "6-20"),
        (21, "21-100"),
        (100, "21-100"),
        (101, "100+"),
    ],
)
def test_count_bucket_edges(value: int, expected: str) -> None:
    assert count_bucket(value) == expected


def test_sensitive_aggregate_bucket_edges() -> None:
    mib = 1024**2
    gib = 1024**3
    assert [
        db_size_bucket(value) for value in (None, 0, 100 * mib - 1, 100 * mib, gib, 5 * gib, 10 * gib, 50 * gib)
    ] == [
        "unknown",
        "<100MB",
        "<100MB",
        "100MB-1GB",
        "1-5GB",
        "5-10GB",
        "10-50GB",
        "50GB+",
    ]
    assert [cost_bucket(value) for value in (0, 9.99, 10, 99.99, 100, 999.99, 1_000, 10_000, 50_000)] == [
        "<10",
        "<10",
        "10-100",
        "10-100",
        "100-1k",
        "100-1k",
        "1k-10k",
        "10k-50k",
        "50k+",
    ]
    assert [output_tokens_bucket(value) for value in (0, 249, 250, 999, 1_000, 3_999, 4_000, 15_999, 16_000)] == [
        "<250",
        "<250",
        "250-1k",
        "250-1k",
        "1k-4k",
        "1k-4k",
        "4k-16k",
        "4k-16k",
        "16k+",
    ]


@pytest.mark.asyncio
async def test_unmeasurable_database_size_is_unknown_and_logs_original_exception(
    async_session: AsyncSession,
    monkeypatch,
    caplog,
) -> None:
    error = OSError("stat denied")
    target = Path("/tmp/telemetry-unmeasurable-db.sqlite3")
    real_stat = Path.stat

    def fail_stat(path: Path, *, follow_symlinks: bool = True):
        if path == target:
            raise error
        return real_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr("app.modules.telemetry.snapshot.sqlite_db_path_from_url", lambda _url: str(target))
    monkeypatch.setattr(Path, "stat", fail_stat)
    builder = TelemetrySnapshotBuilder(async_session)

    with caplog.at_level(logging.DEBUG, logger="app.modules.telemetry.snapshot"):
        size = await builder._database_size_bytes()

    assert size is None
    assert db_size_bucket(size) == "unknown"
    assert caplog.records[-1].exc_info is not None
    assert caplog.records[-1].exc_info[1] is error


@pytest.mark.asyncio
async def test_privacy_quick_check_identifying_values_never_serialize(async_session: AsyncSession) -> None:
    encryptor = TokenEncryptor()
    account = Account(
        id="account-private-id",
        email="alice@corp.com",
        workspace_id="W1",
        plan_type="team",
        access_token_encrypted=encryptor.encrypt("access-private"),
        refresh_token_encrypted=encryptor.encrypt("refresh-private"),
        id_token_encrypted=encryptor.encrypt("id-private"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    async_session.add(account)
    async_session.add(
        ApiKey(
            id="api-key-private-id",
            name="private-key-name",
            key_hash="super-secret-api-key-hash",
            key_prefix="sk-private",
            is_active=True,
        )
    )
    async_session.add(
        ModelSource(
            id="private-source-id",
            name="private-source-name",
            base_url="https://private.example.test",
            api_key_encrypted=encryptor.encrypt("source-api-key"),
            is_enabled=True,
        )
    )
    async_session.add(
        _request_log(
            "privacy",
            account_id=account.id,
            model="corp-internal-gpt",
            useragent_group="senpi",
            useragent="senpi/1.0 alice@corp.com",
            client_ip="192.0.2.9",
            # Error status so the private code exercises the top-errors
            # sanitizer; cancelled/success rows are excluded from that metric.
            status="error",
            error_message="free text alice W1 super-secret-api-key-hash",
            upstream_error_code="private-upstream-message",
        )
    )
    await async_session.commit()

    serialized = (
        await TelemetrySnapshotBuilder(async_session).build(
            "00000000-0000-4000-8000-000000000003",
            consent="undecided",
        )
    ).model_dump_json()

    for private_value in (
        "alice",
        "corp.com",
        "W1",
        "corp-internal-gpt",
        "senpi",
        "192.0.2.9",
        "super-secret-api-key-hash",
        "private-source-name",
        "private-source-id",
        "private-upstream-message",
    ):
        assert private_value not in serialized
    assert '"pool_bucket":"1"' in serialized
    assert '"workspace_accounts":true' in serialized
    assert '"name":"other"' in serialized
    assert '"clients":{"other":1.0}' in serialized
    assert '"top_upstream_errors":["other"]' in serialized


@pytest.mark.asyncio
async def test_success_rate_excludes_cancelled_terminals(async_session: AsyncSession) -> None:
    async_session.add(_request_log("ok", model="gpt-5.4", useragent_group="codex_exec"))
    async_session.add(
        _request_log(
            "cancel-1",
            model="gpt-5.4",
            useragent_group="codex_exec",
            status="cancelled",
            upstream_error_code="client_disconnected",
        )
    )
    async_session.add(
        _request_log(
            "cancel-2",
            model="gpt-5.4",
            useragent_group="codex_exec",
            status="cancelled",
            upstream_error_code="client_disconnected",
        )
    )
    async_session.add(
        _request_log(
            "err",
            model="gpt-5.4",
            useragent_group="codex_exec",
            status="error",
            upstream_error_code="server_error",
        )
    )
    await async_session.commit()

    snapshot = await TelemetrySnapshotBuilder(async_session).build(
        "00000000-0000-4000-8000-000000000004",
        consent="undecided",
    )

    # 1 success out of 4 requests: cancellations are neither successes nor
    # errors, so they must not inflate the numerator.
    assert snapshot.usage_7d.success_rate == 0.25


@pytest.mark.asyncio
async def test_top_upstream_errors_exclude_cancelled_terminals(async_session: AsyncSession) -> None:
    for index in range(3):
        async_session.add(
            _request_log(
                f"cancel-{index}",
                model="gpt-5.4",
                useragent_group="codex_exec",
                status="cancelled",
                upstream_error_code="client_disconnected",
            )
        )
    async_session.add(
        _request_log(
            "err",
            model="gpt-5.4",
            useragent_group="codex_exec",
            status="error",
            upstream_error_code="server_error",
        )
    )
    await async_session.commit()

    snapshot = await TelemetrySnapshotBuilder(async_session).build(
        "00000000-0000-4000-8000-000000000005",
        consent="undecided",
    )

    # High-volume disconnects (status='cancelled' with a retained
    # client_disconnected code) must not displace genuine upstream failures.
    assert snapshot.usage_7d.top_upstream_errors == ["server_error"]
