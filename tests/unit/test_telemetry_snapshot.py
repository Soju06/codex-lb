from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import get_args

import pytest
from pydantic import create_model
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.balancer.logic import RoutingStrategy
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.openai.model_registry import ModelRegistry
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, ApiKey, Base, ModelSource, RequestLog
from app.modules.reports.repository import ReportsRepository
from app.modules.telemetry.clients import (
    CANONICAL_CLIENT_FAMILIES,
    CLIENT_FAMILY_BY_RAW_GROUP,
    ClientCount,
    client_family,
    client_shares,
)
from app.modules.telemetry.schemas import (
    TelemetryActivation,
    TelemetryDay,
    TelemetryOptOut,
    TelemetryRegistration,
    build_snapshot_envelope,
)
from app.modules.telemetry.sender import _json_bytes
from app.modules.telemetry.snapshot import (
    _ROUTING_POLICIES,
    TelemetrySnapshotBuilder,
    _build_day_from_rows,
    _canonical_routing_policy,
    cost_bucket,
    count_bucket,
    db_size_bucket,
    output_tokens_bucket,
)

pytestmark = pytest.mark.unit


@pytest.fixture
async def hostile_bodies(async_session, monkeypatch):
    registry = ModelRegistry()
    official = registry.get_models_with_fallback()["gpt-5.6-sol"]
    private = replace(official, slug="corp-project-alice-private")
    await registry.update({"plus": [official, private]})
    monkeypatch.setattr("app.core.openai.model_registry._model_registry", registry)
    live_catalog = registry.get_snapshot()
    assert live_catalog is not None
    assert "corp-project-alice-private" in live_catalog.models
    now = datetime(2026, 9, 10, 12)
    monkeypatch.setattr("app.modules.telemetry.snapshot.utcnow", lambda: now)
    rows = [
        _request_log(
            "private",
            model=private.slug,
            useragent_group="private-client-group",
            status="error",
            upstream_error_code="private-error-code",
            failure_phase="private-phase",
            upstream_status_code=429,
            failure_detail="private-failure-detail",
            failure_exception_type="PrivateException",
            error_message="private-message",
        ),
        _request_log(
            "official",
            model=official.slug,
            useragent_group="codex_exec",
            status="error",
            upstream_error_code="server_error",
            failure_phase="upstream",
            upstream_status_code=503,
        ),
        _request_log("success", model=official.slug, useragent_group="codex_exec", upstream_status_code=200),
    ]
    for row in rows:
        row.requested_at = now - timedelta(days=1)
        row.upstream_transport = "websocket"
        row.transport = "websocket"
    async_session.add_all(rows)
    await async_session.commit()
    builder = TelemetrySnapshotBuilder(async_session)
    heartbeat = build_snapshot_envelope(await builder.build("instance", consent="enabled"))
    day = await builder.build_day("instance", date(2026, 9, 9))
    return heartbeat, day


@pytest.mark.asyncio
@pytest.mark.parametrize("body_index", [0, 1], ids=["heartbeat", "day"])
async def test_live_private_model_never_reaches_wire(hostile_bodies, body_index: int) -> None:
    wire = _json_bytes(hostile_bodies[body_index])
    for forbidden in (
        b"corp-project-alice-private",
        b"private-client-group",
        b"private-error-code",
        b"private-phase",
        b"private-failure-detail",
        b"PrivateException",
        b"private-message",
    ):
        assert forbidden not in wire
    assert b'"other"' in wire
    assert b'"gpt-5.6-sol"' in wire


@pytest.mark.parametrize(
    ("stored", "expected"),
    [("websocket", "ws"), ("openai_compatible_http", "http"), ("http", "http"), ("private", "other")],
)
def test_day_upstream_transport_uses_producer_spellings(stored: str, expected: str) -> None:
    row = _request_log("transport", model="gpt-5.6-sol", useragent_group="codex_exec", upstream_transport=stored)
    row.transport = "websocket"
    day = _build_day_from_rows("instance", date(2026, 9, 9), [row])
    assert [entry.name for entry in day.dimensions.upstream_transport] == [expected]
    assert [entry.name for entry in day.dimensions.transport] == ["ws"]


@pytest.mark.parametrize(
    ("latency", "ttft", "expected_samples"),
    [(1000, None, 0), (None, 100, 0), (100, 100, 0), (100, 200, 0), (1000, 0, 1)],
)
def test_day_tps_requires_measured_duration(latency, ttft, expected_samples: int) -> None:
    row = _request_log("tps", model="gpt-5.6-sol", useragent_group="codex_exec")
    row.latency_ms = latency
    row.latency_first_token_ms = ttft
    day = _build_day_from_rows("instance", date(2026, 9, 9), [row])
    for entry in [
        day.dimensions.global_,
        *day.dimensions.models,
        *day.dimensions.clients,
        *day.dimensions.transport,
        *day.dimensions.upstream_transport,
        *day.dimensions.service_tier,
    ]:
        assert entry.tps.sample_count == expected_samples
    if ttft == 0:
        assert day.dimensions.global_.ttft_ms.model_dump() == {"sample_count": 1, "buckets": {"0": 1}}
        assert day.dimensions.global_.tps.model_dump() == {"sample_count": 1, "buckets": {"6": 1}}


def _assert_day_wire_keys(body) -> None:
    assert set(body) == {"schema_version", "instance_id", "utc_date", "dimensions", "errors"}
    dimensions = body["dimensions"]
    marginal_names = {"models", "clients", "transport", "upstream_transport", "service_tier"}
    assert set(dimensions) == marginal_names | {"global", "request_kinds"}
    assert set(dimensions["request_kinds"]) == {"responses", "chat", "images", "unknown"}
    for name in marginal_names:
        assert dimensions[name], f"fixture must populate {name}"
    for entry in [dimensions["global"], *(entry for name in marginal_names for entry in dimensions[name])]:
        assert set(entry) == {"name", "requests", "latency_ms", "ttft_ms", "tps"}
        for metric in ("latency_ms", "ttft_ms", "tps"):
            histogram = entry[metric]
            assert set(histogram) == {"sample_count", "buckets"}
            assert set(histogram["buckets"]) <= {str(i) for i in range(11 if metric == "tps" else 14)}
            assert all(type(count) is int and count > 0 for count in histogram["buckets"].values())
            assert histogram["sample_count"] == sum(histogram["buckets"].values())
    errors = body["errors"]
    assert set(errors) == {"upstream_error_class", "failure_phase", "http_status_class", "outcomes"}
    assert set(errors["upstream_error_class"]) == {"other", "server_error"}
    assert set(errors["failure_phase"]) == {"other", "stream"}
    assert set(errors["http_status_class"]) == {"2xx", "429", "5xx"}
    assert set(errors["outcomes"]) == {"success", "error", "cancelled"}
    for counts in errors.values():
        assert all(type(count) is int and count >= 0 for count in counts.values())


@pytest.mark.asyncio
async def test_populated_day_sender_wire_has_exact_recursive_allowlist(hostile_bodies) -> None:
    _assert_day_wire_keys(json.loads(_json_bytes(hostile_bodies[1])))


@pytest.mark.asyncio
async def test_day_wire_allowlist_detects_undeclared_python_model_field(hostile_bodies) -> None:
    extended_model = create_model(
        "ExtendedTelemetryDay", __base__=TelemetryDay, private_tag=(str, "undeclared-private-value")
    )
    extended = extended_model.model_validate(hostile_bodies[1].model_dump(by_alias=True))
    wire = _json_bytes(extended)
    assert b"undeclared-private-value" in wire
    with pytest.raises(AssertionError):
        _assert_day_wire_keys(json.loads(wire))


@pytest.mark.parametrize(
    ("stored", "wire_phase"),
    [("usage_settlement", "settle"), ("upstream", "stream"), ("bridge", "bridge_queue"), ("private-phase", "other")],
)
def test_day_failure_phase_uses_producer_spellings(stored: str, wire_phase: str) -> None:
    row = _request_log("phase", model="gpt-5.6-sol", useragent_group="codex_exec", status="error", failure_phase=stored)
    day = _build_day_from_rows("instance", date(2026, 9, 9), [row])
    assert day.errors.failure_phase == {wire_phase: 1}


@pytest.mark.asyncio
async def test_day_excludes_warmups_like_reports_and_heartbeat(async_session, monkeypatch) -> None:
    now = datetime(2026, 9, 10, 12)
    monkeypatch.setattr("app.modules.telemetry.snapshot.utcnow", lambda: now)
    rows = [
        _request_log("real", model="gpt-5.6-sol", useragent_group="codex_exec", request_kind="normal"),
        _request_log("warmup", model="gpt-5.6-sol", useragent_group="codex_exec", request_kind="warmup"),
        _request_log("limit-kind", model="gpt-5.6-sol", useragent_group="codex_exec", request_kind="limit_warmup"),
        _request_log("limit-source", model="gpt-5.6-sol", useragent_group="codex_exec", source="limit_warmup"),
    ]
    for row in rows:
        row.requested_at = now - timedelta(days=1)
    only_warmup = _request_log(
        "warmup-only-day", model="gpt-5.6-sol", useragent_group="codex_exec", request_kind="warmup"
    )
    only_warmup.requested_at = now - timedelta(days=2)
    async_session.add_all([*rows, only_warmup])
    await async_session.commit()
    builder = TelemetrySnapshotBuilder(async_session)
    days = await builder.completed_days("instance")
    summary = await ReportsRepository(async_session).aggregate_summary(datetime(2026, 9, 9), datetime(2026, 9, 10))
    heartbeat = await builder.build("instance", consent="enabled")

    assert [day.utc_date for day in days] == [date(2026, 9, 9)]
    assert days[0].dimensions.global_.requests == summary.total_requests == heartbeat.usage_7d.requests == 1
    assert days[0].errors.outcomes.success == 1
    assert days[0].dimensions.global_.latency_ms.sample_count == 1


@pytest.mark.asyncio
async def test_day_query_selects_only_aggregation_scalars(async_session, monkeypatch) -> None:
    now = datetime(2026, 9, 10, 12)
    row = _request_log("selected", model="gpt-5.6-sol", useragent_group="codex_exec")
    row.requested_at = now - timedelta(days=1)
    async_session.add(row)
    await async_session.commit()
    captured = []
    original_execute = async_session.execute

    async def capture(statement, *args, **kwargs):
        captured.append(statement)
        return await original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(async_session, "execute", capture)
    await TelemetrySnapshotBuilder(async_session).build_day("instance", date(2026, 9, 9), today_utc=now.date())
    selected = {column.key for column in captured[0].selected_columns}
    assert selected == {
        "model",
        "useragent_group",
        "transport",
        "upstream_transport",
        "service_tier",
        "actual_service_tier",
        "status",
        "upstream_error_code",
        "failure_phase",
        "upstream_status_code",
        "latency_ms",
        "latency_first_token_ms",
        "output_tokens",
        "reasoning_tokens",
    }
    assert not selected & {"useragent", "error_message", "failure_detail", "failure_exception_type"}


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
        "total",
        "per_plan",
        "per_status",
        "workspace_accounts",
        "routing_policy",
        "limit_warmup_enabled",
        "egress_proxy_used",
    }
    assert set(payload["accounts"]["per_plan"]) == {"plus", "pro", "team", "free"}
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
    assert '"total":1' in serialized
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
async def test_completed_day_outcomes_partition_all_rows(async_session: AsyncSession) -> None:
    requested_at = utcnow() - timedelta(days=1)
    rows = [
        _request_log(f"outcome-{index}", model="gpt-5.4", useragent_group="codex_exec", status=status)
        for index, status in enumerate(["success", "success", "error", "cancelled", "cancelled"])
    ]
    for row in rows:
        row.requested_at = requested_at
    async_session.add_all(rows)
    await async_session.commit()

    day = await TelemetrySnapshotBuilder(async_session).build_day("instance", requested_at.date())

    assert day.errors.outcomes.model_dump() == {"success": 2, "error": 1, "cancelled": 2}
    assert sum(day.errors.outcomes.model_dump().values()) == len(rows)


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


@pytest.mark.asyncio
async def test_features_automations_reads_the_effective_dashboard_toggle(async_session: AsyncSession) -> None:
    """M2 background jobs: a dashboard pause (column False, env True) turns the feature off."""
    from app.db.models import AutomationJob, DashboardSettings
    from app.modules.settings.repository import SettingsRepository

    async_session.add(
        AutomationJob(
            id="job-1",
            name="ping",
            enabled=True,
            schedule_time="05:00",
            schedule_timezone="UTC",
            model="gpt-5.6-sol",
        )
    )
    await async_session.commit()
    row = await SettingsRepository(async_session).get_or_create()

    startup = get_settings().model_copy(update={"automations_scheduler_enabled": True})
    enabled = await TelemetrySnapshotBuilder(async_session, settings=startup).build(
        "00000000-0000-4000-8000-000000000002", consent="undecided"
    )
    assert enabled.features.automations is True

    row.automations_scheduler_enabled = False
    await async_session.commit()
    paused = await TelemetrySnapshotBuilder(async_session, settings=startup).build(
        "00000000-0000-4000-8000-000000000002", consent="undecided"
    )
    assert paused.features.automations is False
    assert (await async_session.get(DashboardSettings, 1)) is row


# M5 conversation archive
@pytest.mark.asyncio
async def test_features_conversation_archive_follows_the_dashboard_value(async_session: AsyncSession) -> None:
    """The feature flag reports the effective toggle (dashboard column over the env alias)."""
    from app.core.config.settings import get_settings
    from app.modules.settings.repository import SettingsRepository

    row = await SettingsRepository(async_session).get_or_create()
    row.conversation_archive_enabled = True
    await async_session.commit()
    environment = get_settings().model_copy(update={"conversation_archive_enabled": False})

    snapshot = await TelemetrySnapshotBuilder(async_session, settings=environment).build(
        "00000000-0000-4000-8000-000000000001",
        consent="enabled",
    )

    assert snapshot.features.conversation_archive is True


# end M5 conversation archive
