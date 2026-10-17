from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, ApiKey
from app.db.session import SessionLocal, engine
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
async def test_request_logs_options_include_cancelled_status(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_opt_cancelled", "cancelled@example.com"))
        await logs_repo.add_log(
            account_id="acc_opt_cancelled",
            request_id="req_opt_cancelled",
            model="gpt-5.1",
            input_tokens=1,
            output_tokens=0,
            latency_ms=10,
            status="cancelled",
            error_code="client_disconnected",
            requested_at=now,
        )

    response = await async_client.get("/api/request-logs/options")

    assert response.status_code == 200
    assert response.json()["statuses"] == ["cancelled"]


@pytest.mark.asyncio
async def test_request_logs_options_returns_distinct_accounts_and_models(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_opt_a", "a@example.com"))
        await accounts_repo.upsert(_make_account("acc_opt_b", "b@example.com"))

        await logs_repo.add_log(
            account_id="acc_opt_a",
            request_id="req_opt_1",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=10,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=1),
        )
        await logs_repo.add_log(
            account_id="acc_opt_b",
            request_id="req_opt_2",
            model="gpt-4o",
            input_tokens=10,
            output_tokens=10,
            latency_ms=100,
            status="error",
            error_code="rate_limit_exceeded",
            error_message="Rate limit reached",
            requested_at=now,
        )

    response = await async_client.get("/api/request-logs/options")
    assert response.status_code == 200
    payload = response.json()
    assert payload["accountIds"] == ["acc_opt_a", "acc_opt_b"]
    assert payload["apiKeys"] == []
    assert payload["modelOptions"] == [
        {"model": "gpt-4o", "reasoningEffort": None},
        {"model": "gpt-5.1", "reasoningEffort": None},
    ]
    assert payload["statuses"] == ["ok", "rate_limit"]


@pytest.mark.asyncio
async def test_request_logs_options_ignores_status_self_filter(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_opt_ok", "ok@example.com"))
        await accounts_repo.upsert(_make_account("acc_opt_err", "err@example.com"))

        await logs_repo.add_log(
            account_id="acc_opt_ok",
            request_id="req_opt_ok",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=10,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now,
        )
        await logs_repo.add_log(
            account_id="acc_opt_err",
            request_id="req_opt_err",
            model="gpt-4o",
            input_tokens=10,
            output_tokens=10,
            latency_ms=100,
            status="error",
            error_code="rate_limit_exceeded",
            requested_at=now,
        )

    response = await async_client.get("/api/request-logs/options?status=ok")
    assert response.status_code == 200
    payload = response.json()
    assert payload["accountIds"] == ["acc_opt_err", "acc_opt_ok"]
    assert payload["apiKeys"] == []
    assert payload["modelOptions"] == [
        {"model": "gpt-4o", "reasoningEffort": None},
        {"model": "gpt-5.1", "reasoningEffort": None},
    ]
    assert payload["statuses"] == ["ok", "rate_limit"]


@pytest.mark.asyncio
async def test_request_logs_options_ignore_status_matches_unfiltered_response(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_opt_ok_2", "ok2@example.com"))
        await accounts_repo.upsert(_make_account("acc_opt_quota", "quota@example.com"))

        await logs_repo.add_log(
            account_id="acc_opt_ok_2",
            request_id="req_opt_ok_2",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=10,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now,
        )
        await logs_repo.add_log(
            account_id="acc_opt_quota",
            request_id="req_opt_quota",
            model="gpt-5.2",
            input_tokens=10,
            output_tokens=10,
            latency_ms=100,
            status="error",
            error_code="insufficient_quota",
            requested_at=now,
        )

    base = await async_client.get("/api/request-logs/options")
    with_status = await async_client.get("/api/request-logs/options?status=ok&status=quota")

    assert base.status_code == 200
    assert with_status.status_code == 200
    assert with_status.json() == base.json()


@pytest.mark.asyncio
async def test_request_logs_options_respects_non_status_filters(async_client, db_setup):
    now = utcnow()
    old = now - timedelta(days=10)
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_scope_a", "scope-a@example.com"))
        await accounts_repo.upsert(_make_account("acc_scope_b", "scope-b@example.com"))

        await logs_repo.add_log(
            account_id="acc_scope_a",
            request_id="req_scope_1",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=10,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now,
        )
        await logs_repo.add_log(
            account_id="acc_scope_a",
            request_id="req_scope_2",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=10,
            latency_ms=100,
            status="error",
            error_code="rate_limit_exceeded",
            requested_at=now,
        )
        await logs_repo.add_log(
            account_id="acc_scope_b",
            request_id="req_scope_3",
            model="gpt-5.2",
            input_tokens=10,
            output_tokens=10,
            latency_ms=100,
            status="error",
            error_code="insufficient_quota",
            requested_at=old,
        )

    scoped = await async_client.get(
        "/api/request-logs/options"
        "?accountId=acc_scope_a"
        "&modelOption=gpt-5.1:::"
        f"&since={(now - timedelta(hours=1)).isoformat()}"
    )

    assert scoped.status_code == 200
    payload = scoped.json()
    assert payload["accountIds"] == ["acc_scope_a"]
    assert payload["apiKeys"] == []
    assert payload["modelOptions"] == [{"model": "gpt-5.1", "reasoningEffort": None}]
    assert payload["statuses"] == ["ok", "rate_limit"]


@pytest.mark.asyncio
async def test_request_logs_options_return_api_keys_and_ignore_api_key_self_filter(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_key_opt", "key-opt@example.com"))
        session.add_all(
            [
                ApiKey(
                    id="key_opt_a",
                    name="Alpha Key",
                    key_hash="hash_key_opt_a",
                    key_prefix="sk-alpha",
                ),
                ApiKey(
                    id="key_opt_b",
                    name="Beta Key",
                    key_hash="hash_key_opt_b",
                    key_prefix="sk-beta",
                ),
            ]
        )
        await session.commit()

        await logs_repo.add_log(
            account_id="acc_key_opt",
            request_id="req_key_opt_1",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=10,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=1),
            api_key_id="key_opt_a",
        )
        await logs_repo.add_log(
            account_id="acc_key_opt",
            request_id="req_key_opt_2",
            model="gpt-5.2",
            input_tokens=10,
            output_tokens=10,
            latency_ms=100,
            status="error",
            error_code="rate_limit_exceeded",
            requested_at=now,
            api_key_id="key_opt_b",
        )

    response = await async_client.get("/api/request-logs/options?apiKeyId=key_opt_a")
    assert response.status_code == 200
    payload = response.json()
    assert payload["accountIds"] == ["acc_key_opt"]
    assert payload["modelOptions"] == [{"model": "gpt-5.1", "reasoningEffort": None}]
    assert payload["statuses"] == ["ok"]
    assert payload["apiKeys"] == [
        {"id": "key_opt_a", "name": "Alpha Key", "keyPrefix": "sk-alpha"},
        {"id": "key_opt_b", "name": "Beta Key", "keyPrefix": "sk-beta"},
    ]


@pytest.mark.asyncio
async def test_request_logs_options_unfiltered_skip_scan_covers_pair_facets(async_client, db_setup):
    """Unfiltered options use the skip-scan path; pair facets must include
    NULL and non-NULL second columns for the same leading value, in order."""
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_pair", "pair@example.com"))

        for request_id, model, effort, status, error_code in (
            ("req_pair_1", "gpt-5.1", None, "success", None),
            ("req_pair_2", "gpt-5.1", "high", "error", "rate_limit_exceeded"),
            ("req_pair_3", "gpt-5.1", "low", "error", "insufficient_quota"),
            ("req_pair_4", "gpt-4o", "medium", "success", None),
        ):
            await logs_repo.add_log(
                account_id="acc_pair",
                request_id=request_id,
                model=model,
                reasoning_effort=effort,
                input_tokens=10,
                output_tokens=10,
                latency_ms=100,
                status=status,
                error_code=error_code,
                requested_at=now,
            )

    response = await async_client.get("/api/request-logs/options")
    assert response.status_code == 200
    payload = response.json()
    assert payload["accountIds"] == ["acc_pair"]
    # NULL pair placement follows the backend's ASC NULL ordering, matching
    # the legacy DISTINCT path (SQLite: first, PostgreSQL: last).
    null_pair = {"model": "gpt-5.1", "reasoningEffort": None}
    non_null_pairs = [
        {"model": "gpt-5.1", "reasoningEffort": "high"},
        {"model": "gpt-5.1", "reasoningEffort": "low"},
    ]
    gpt51_pairs = [null_pair, *non_null_pairs] if engine.dialect.name == "sqlite" else [*non_null_pairs, null_pair]
    assert payload["modelOptions"] == [{"model": "gpt-4o", "reasoningEffort": "medium"}, *gpt51_pairs]
    assert payload["statuses"] == ["ok", "rate_limit", "quota"]


@pytest.mark.asyncio
async def test_request_logs_options_unfiltered_excludes_soft_deleted(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_live", "live@example.com"))
        await logs_repo.add_log(
            account_id="acc_live",
            request_id="req_live",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=10,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now,
        )
        deleted = await logs_repo.add_log(
            account_id="acc_live",
            request_id="req_deleted",
            model="gpt-ghost",
            input_tokens=10,
            output_tokens=10,
            latency_ms=100,
            status="error",
            error_code="ghost_error",
            requested_at=now,
        )
        deleted.deleted_at = utcnow()
        await session.commit()

    response = await async_client.get("/api/request-logs/options")
    assert response.status_code == 200
    payload = response.json()
    assert payload["modelOptions"] == [{"model": "gpt-5.1", "reasoningEffort": None}]
    assert payload["statuses"] == ["ok"]


@pytest.mark.asyncio
async def test_request_logs_options_empty_table(async_client, db_setup):
    response = await async_client.get("/api/request-logs/options")
    assert response.status_code == 200
    payload = response.json()
    assert payload["accountIds"] == []
    assert payload["modelOptions"] == []
    assert payload["apiKeys"] == []
    assert payload["statuses"] == []


@pytest.mark.asyncio
async def test_request_logs_options_unfiltered_matches_wide_since_filter(async_client, db_setup):
    """Parity oracle: the unfiltered (skip-scan) response must equal a
    since-filtered (legacy DISTINCT) response covering all rows."""
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_par_a", "par-a@example.com"))
        await accounts_repo.upsert(_make_account("acc_par_b", "par-b@example.com"))
        for index, (account_id, model, effort, status, error_code) in enumerate(
            (
                ("acc_par_a", "gpt-5.1", "high", "success", None),
                ("acc_par_a", "gpt-5.1", None, "error", "rate_limit_exceeded"),
                ("acc_par_b", "gpt-4o", "low", "error", "insufficient_quota"),
                ("acc_par_b", "o4-mini", None, "success", None),
            )
        ):
            await logs_repo.add_log(
                account_id=account_id,
                request_id=f"req_par_{index}",
                model=model,
                reasoning_effort=effort,
                input_tokens=10,
                output_tokens=10,
                latency_ms=100,
                status=status,
                error_code=error_code,
                requested_at=now - timedelta(minutes=index),
            )

    unfiltered = await async_client.get("/api/request-logs/options")
    filtered = await async_client.get(f"/api/request-logs/options?since={(now - timedelta(days=1)).isoformat()}")
    assert unfiltered.status_code == 200
    assert filtered.status_code == 200
    assert unfiltered.json() == filtered.json()


@pytest.mark.asyncio
async def test_request_logs_options_unfiltered_preserves_empty_string_second_column(async_client, db_setup):
    """Legacy DISTINCT drops falsy leading values but preserves empty-string
    second columns; the skip-scan path must match."""
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_empty", "empty@example.com"))
        await logs_repo.add_log(
            account_id="acc_empty",
            request_id="req_empty_effort",
            model="gpt-5.1",
            reasoning_effort="",
            input_tokens=10,
            output_tokens=10,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now,
        )

    unfiltered = await async_client.get("/api/request-logs/options")
    filtered = await async_client.get(f"/api/request-logs/options?since={(now - timedelta(days=1)).isoformat()}")
    assert unfiltered.status_code == 200
    assert unfiltered.json() == filtered.json()
    assert unfiltered.json()["modelOptions"] == [{"model": "gpt-5.1", "reasoningEffort": ""}]


@pytest.mark.asyncio
async def test_request_logs_options_unfiltered_returns_api_key_facet(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_key_skip", "key-skip@example.com"))
        session.add_all(
            [
                ApiKey(id="key_skip_a", name="Alpha", key_hash="hash_skip_a", key_prefix="sk-a"),
                ApiKey(id="key_skip_b", name="Beta", key_hash="hash_skip_b", key_prefix="sk-b"),
            ]
        )
        await session.commit()
        for index, key_id in enumerate(("key_skip_a", "key_skip_b", None)):
            await logs_repo.add_log(
                account_id="acc_key_skip",
                request_id=f"req_key_skip_{index}",
                model="gpt-5.1",
                input_tokens=10,
                output_tokens=10,
                latency_ms=100,
                status="success",
                error_code=None,
                requested_at=now,
                api_key_id=key_id,
            )

    response = await async_client.get("/api/request-logs/options")
    assert response.status_code == 200
    assert [key["id"] for key in response.json()["apiKeys"]] == ["key_skip_a", "key_skip_b"]


@pytest.mark.asyncio
async def test_request_logs_options_unfiltered_issues_no_distinct_statements(async_client, db_setup):
    """The spec requires bounded probes for unfiltered facets: no facet may
    run a DISTINCT pass over request_logs."""
    import re

    from sqlalchemy import event

    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_shape", "shape@example.com"))
        for index, model in enumerate(("gpt-4o", "gpt-5.1", "o4-mini")):
            await logs_repo.add_log(
                account_id="acc_shape",
                request_id=f"req_shape_{index}",
                model=model,
                input_tokens=10,
                output_tokens=10,
                latency_ms=100,
                status="success",
                error_code=None,
                requested_at=now,
            )

    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _capture)
    try:
        response = await async_client.get("/api/request-logs/options")
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _capture)

    assert response.status_code == 200
    assert len(response.json()["modelOptions"]) == 3
    options_statements = [stmt for stmt in statements if "request_logs" in stmt]
    assert options_statements, "expected captured facet statements"
    assert not any(re.search(r"SELECT\s+DISTINCT\b", stmt, re.IGNORECASE) for stmt in options_statements)
    assert any("facet_skip" in stmt for stmt in options_statements)


# PostgreSQL plan pin for the live-row partial facet indexes. Seed shape
# mirrors production: thousands of live rows spread over several models, api
# keys and statuses (so a full pass over any live-row index, including the
# partial ``idx_logs_dash_usage_covering``, is materially costlier than a
# probe), soft-deleted rows sharing the live values, and a large soft-deleted
# cohort on values no live row carries, sorting before or between the live
# values (``key_ghost`` < ``key_live_*``, ``gpt-5.1`` < ``gpt-ghost`` <
# ``o4-mini``, ``ghost_error`` < ``rate_limit_exceeded``) so a non-partial
# facet index walks the whole cohort before reaching the next live value
# (measured: 20k ``Rows Removed by Filter`` on ``idx_logs_model_effort_time``
# without the live index versus one index step with it).
_LIVE_FACET_SEED_ROWS = 3_000
_DEAD_SHARED_SEED_ROWS = 3_000
_DEAD_COHORT_SEED_ROWS = 20_000
# Each probe is a single btree step (``LIMIT 1`` MinMax form) on the live-row
# index; the bound is a constant, never the soft-deleted cohort size.
_PROBE_ROW_BOUND = 1
# Statement -> live-row index it must be served by. Every facet statement
# carries the status clause, so the status/error-code pair is matched last;
# account probes have no live index (soft deletion detaches account_id).
_LIVE_FACET_INDEX_BY_COLUMN: tuple[tuple[str, str | None], ...] = (
    ("request_logs.api_key_id", "idx_logs_live_api_key"),
    ("request_logs.model", "idx_logs_live_model_effort"),
    ("request_logs.account_id", None),
    ("request_logs.status", "idx_logs_live_status_error"),
)


_LIVE_FACET_INDEX_NAMES = tuple(name for _column, name in _LIVE_FACET_INDEX_BY_COLUMN if name is not None)


def _expected_live_index(sql: str) -> str | None:
    for column, index_name in _LIVE_FACET_INDEX_BY_COLUMN:
        if column in sql:
            return index_name
    raise AssertionError(f"unrecognised facet statement: {sql}")


async def _seed_live_facet_corpus() -> None:
    """Bulk-seed the plan-pin corpus with PostgreSQL ``generate_series``."""
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        await accounts_repo.upsert(_make_account("acc_facets", "facets@example.com"))
        session.add(ApiKey(id="key_live_a", name="Live A", key_hash="hash_live_a", key_prefix="sk-la"))
        session.add(ApiKey(id="key_live_b", name="Live B", key_hash="hash_live_b", key_prefix="sk-lb"))
        await session.commit()
        live_values = (
            "(ARRAY['key_live_a', 'key_live_b'])[1 + n % 2], "
            "(ARRAY['gpt-4o', 'gpt-5.1', 'o4-mini'])[1 + n % 3], "
            "CASE WHEN (n / 3) % 2 = 0 THEN 'high' END, "
            "(ARRAY['success', 'error', 'cancelled'])[1 + (n / 6) % 3], "
            "CASE WHEN (n / 6) % 3 = 1 AND n % 2 = 0 THEN 'rate_limit_exceeded' END"
        )
        columns = (
            "(account_id, api_key_id, request_id, requested_at, deleted_at, "
            "model, reasoning_effort, status, error_code)"
        )
        await session.execute(
            text(
                f"INSERT INTO request_logs {columns} "
                "SELECT 'acc_facets', k, 'req_live_' || n, now() - n * interval '1 second', NULL, m, e, s, c "
                f"FROM generate_series(1, :rows) AS n, LATERAL (SELECT {live_values}) AS v(k, m, e, s, c)"
            ),
            {"rows": _LIVE_FACET_SEED_ROWS},
        )
        # Soft-deleted rows sharing the live values, older than the live rows.
        await session.execute(
            text(
                f"INSERT INTO request_logs {columns} "
                "SELECT NULL, k, 'req_dead_shared_' || n, now() - interval '30 days' - n * interval '1 second', "
                "now(), m, e, s, c "
                f"FROM generate_series(1, :rows) AS n, LATERAL (SELECT {live_values}) AS v(k, m, e, s, c)"
            ),
            {"rows": _DEAD_SHARED_SEED_ROWS},
        )
        # Soft-deleted cohort on values no live row carries.
        await session.execute(
            text(
                f"INSERT INTO request_logs {columns} "
                "SELECT NULL, 'key_ghost', 'req_dead_cohort_' || n, "
                "now() - interval '30 days' - n * interval '1 second', now(), "
                "'gpt-ghost', 'high', 'error', 'ghost_error' FROM generate_series(1, :rows) AS n"
            ),
            {"rows": _DEAD_COHORT_SEED_ROWS},
        )
        await session.commit()
    autocommit_engine = engine.execution_options(isolation_level="AUTOCOMMIT")
    async with autocommit_engine.connect() as conn:
        await conn.execute(text("VACUUM (ANALYZE) request_logs"))


def _plan_nodes(plan: dict) -> list[dict]:
    nodes = [plan]
    for child in plan.get("Plans", ()):
        nodes.extend(_plan_nodes(child))
    return nodes


@pytest.mark.asyncio
@pytest.mark.skipif(engine.dialect.name != "postgresql", reason="PostgreSQL-only query plan test")
async def test_request_logs_options_facet_probes_use_live_row_indexes_postgresql(async_client, db_setup):
    """Every production ``facet_skip`` statement (leading skip scan, per-value
    second-column skip scan and ``(value, NULL)`` probe) for the model, api-key
    and status facets is served by its live-row partial index: no rows removed
    by filter and a constant number of index entries per probe, independent of
    the soft-deleted cohort. The statements are captured from the real options
    call and re-run under EXPLAIN ANALYZE rather than hand-written."""
    from sqlalchemy import event
    from sqlalchemy.sql import Select

    await _seed_live_facet_corpus()

    captured: list[str] = []

    def _capture(conn, clauseelement, multiparams, params, execution_options):
        if isinstance(clauseelement, Select):
            compiled = clauseelement.compile(dialect=engine.dialect, compile_kwargs={"literal_binds": True})
            captured.append(str(compiled))

    event.listen(engine.sync_engine, "before_execute", _capture)
    try:
        response = await async_client.get("/api/request-logs/options")
    finally:
        event.remove(engine.sync_engine, "before_execute", _capture)

    assert response.status_code == 200
    payload = response.json()
    assert payload["accountIds"] == ["acc_facets"]
    assert sorted({option["model"] for option in payload["modelOptions"]}) == ["gpt-4o", "gpt-5.1", "o4-mini"]
    assert [key["id"] for key in payload["apiKeys"]] == ["key_live_a", "key_live_b"]
    assert payload["statuses"] == ["ok", "cancelled", "rate_limit", "error"]

    facet_statements = [sql for sql in captured if "FROM request_logs" in sql]
    assert any("facet_skip" in sql for sql in facet_statements)
    # Leading skip scans, second-column skip scans and (value, NULL) probes.
    assert any("request_logs.reasoning_effort IS NULL" in sql for sql in facet_statements)
    assert any("min(request_logs.reasoning_effort)" in sql for sql in facet_statements)
    assert any("min(request_logs.error_code)" in sql for sql in facet_statements)

    async with SessionLocal() as session:
        live_row_indexes = {
            str(name)
            for (name,) in (
                await session.execute(
                    text(
                        "SELECT indexname FROM pg_indexes WHERE tablename = 'request_logs' "
                        "AND indexdef LIKE '%WHERE (deleted_at IS NULL)'"
                    )
                )
            ).all()
        }
        assert set(_LIVE_FACET_INDEX_NAMES) <= live_row_indexes
        for sql in facet_statements:
            expected_index = _expected_live_index(sql)
            plan_rows = await session.execute(
                text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql.replace(":", r"\:"))
            )
            plan_document = plan_rows.scalar_one()
            if isinstance(plan_document, str):
                plan_document = json.loads(plan_document)
            scans = [
                node for node in _plan_nodes(plan_document[0]["Plan"]) if node.get("Relation Name") == "request_logs"
            ]
            assert scans, f"no request_logs scan in plan for {sql}"
            if expected_index is None:
                continue
            index_names = {node.get("Index Name") for node in scans}
            if "min(" not in sql:
                # ``(value, NULL)`` existence probe: LIMIT-1 costed, so the
                # planner may take any index whose predicate excludes the
                # soft-deleted rows (the facet index or the partial covering
                # index); either way the cohort is never walked.
                assert index_names <= live_row_indexes, f"{sql}: {scans}"
                assert all(int(node.get("Actual Rows", 0)) <= _PROBE_ROW_BOUND for node in scans), f"{sql}: {scans}"
                continue
            assert index_names == {expected_index}, f"{sql}: {scans}"
            assert sum(int(node.get("Rows Removed by Filter", 0)) for node in scans) == 0, f"{sql}: {scans}"
            assert all(int(node.get("Actual Rows", 0)) <= _PROBE_ROW_BOUND for node in scans), f"{sql}: {scans}"
