"""Synthetic PostgreSQL fixture behind the benchmark command."""

from __future__ import annotations

from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


def seed_fixture(engine: Engine, *, rows: int, accounts: int, seed: int) -> dict[str, Any]:
    timestamp = "TIMESTAMP '2026-01-01 00:00:00'"
    account_values = {
        "id": "'benchmark-account-' || i",
        "codex_installation_id": "md5('benchmark-installation-' || i)",
        "email": "'benchmark-' || i || '@example.invalid'",
        "plan_type": "'plus'",
        "access_token_encrypted": "decode('00', 'hex')",
        "refresh_token_encrypted": "decode('00', 'hex')",
        "id_token_encrypted": "decode('00', 'hex')",
        "last_refresh": timestamp,
        "created_at": timestamp,
        "status": "'active'",
    }
    request_values = {
        "account_id": "'benchmark-account-' || (1 + ((i + :seed) % :accounts))",
        "request_id": "'benchmark-request-' || i",
        "requested_at": f"{timestamp} + ((i + :seed) % 2592000) * INTERVAL '1 second'",
        "model": "(ARRAY['gpt-5', 'gpt-5-mini', 'gpt-5-codex'])[1 + ((i + :seed) % 3)]",
        "status": "CASE WHEN (i + :seed) % 10 = 0 THEN 'error' ELSE 'success' END",
        "transport": "CASE WHEN (i + :seed) % 2 = 0 THEN 'http' ELSE 'websocket' END",
        "input_tokens": "100 + ((i + :seed) % 1000)",
        "output_tokens": "10 + ((i + :seed) % 100)",
        "cached_input_tokens": "(i + :seed) % 100",
        "reasoning_tokens": "(i + :seed) % 10",
        "latency_ms": "100 + ((i + :seed) % 5000)",
        "useragent": "(ARRAY['codex_cli_rs/1.0', 'codex_vscode/1.0', 'unknown', NULL])[1 + ((i + :seed) % 4)]",
        "cost_usd": "CASE WHEN (i + :seed) % 2 = 0 THEN NULL ELSE 0.01 END",
        "service_tier": "CASE WHEN (i + :seed) % 5 = 0 THEN 'priority' ELSE 'default' END",
    }
    parameters = {"rows": rows, "accounts": accounts, "seed": seed}
    included: dict[str, list[str]] = {}
    with engine.begin() as connection:
        for table, values, count in (("accounts", account_values, accounts), ("request_logs", request_values, rows)):
            columns = {column["name"] for column in inspect(connection).get_columns(table)}
            selected = {name: expression for name, expression in values.items() if name in columns}
            included[table] = list(selected)
            connection.execute(
                text(
                    f"INSERT INTO {table} ({', '.join(selected)}) "
                    f"SELECT {', '.join(selected.values())} FROM generate_series(1::bigint, :count) AS i"
                ),
                {**parameters, "count": count},
            )
        connection.execute(text("ANALYZE accounts"))
        connection.execute(text("ANALYZE request_logs"))
        observed = {
            "rows": connection.execute(text("SELECT count(*) FROM request_logs")).scalar_one(),
            "accounts": connection.execute(text("SELECT count(*) FROM accounts")).scalar_one(),
            "by_status": dict(
                connection.execute(text("SELECT status, count(*) FROM request_logs GROUP BY status")).tuples().all()
            ),
            "by_model": dict(
                connection.execute(text("SELECT model, count(*) FROM request_logs GROUP BY model")).tuples().all()
            ),
            "by_account": dict(
                connection.execute(text("SELECT account_id, count(*) FROM request_logs GROUP BY account_id"))
                .tuples()
                .all()
            ),
        }
        for column, name, predicate in (
            ("cost_usd", "missing_cost_rows", "cost_usd IS NULL"),
            ("useragent", "useragent_with_slash_rows", "position('/' in useragent) > 0"),
        ):
            if column in included["request_logs"]:
                observed[name] = connection.execute(
                    text(f"SELECT count(*) FROM request_logs WHERE {predicate}")
                ).scalar_one()
    return {
        "version": "modulo-v1",
        **parameters,
        "included_columns": included,
        "observed": observed,
        "distribution": "i=1..rows; offset=i+seed; uniform accounts and 3 models; 10% errors; alternating transports; "
        "4 user-agent buckets including null; half missing costs; 20% priority; "
        "timestamps within 30 days of 2026-01-01",
    }
