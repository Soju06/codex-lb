# Tasks — Upstream Transport Observability

## 1. Spec and schema

- [x] 1.1 Add OpenSpec requirements for persisted upstream transport evidence, Request Logs API exposure, and low-cardinality metrics.
- [x] 1.2 Add an Alembic migration for nullable `request_logs.upstream_transport` on the current head; downgrade drops it.
- [x] 1.3 Add the ORM field and repository/mapping/schema plumbing.

## 2. Request logging

- [x] 2.1 Extend `_write_request_log` / repository `add_log` to accept `upstream_transport` while preserving `transport` as downstream transport.
- [x] 2.2 Pass upstream transport from the streaming Responses final request-log path.
- [x] 2.3 Leave non-streaming or unrelated request kinds as `null` unless their upstream transport is known.

## 3. Metrics

- [x] 3.1 Add `codex_lb_upstream_transport_decisions_total` with labels `downstream_transport`, `upstream_transport`, `policy`, `sticky`, and `status`.
- [x] 3.2 Increment it once per completed streaming Responses request after final status is known.
- [x] 3.3 Ensure metric helpers no-op cleanly when `prometheus_client` is unavailable.

## 4. Tests

- [x] 4.1 RED/GREEN integration test: Request Logs API exposes `upstream_transport` from persisted rows.
- [x] 4.2 RED/GREEN route-level test: smart single-shot persists `upstream_transport = "http"`; sticky smart preserves `upstream_transport = "auto"` when base is auto.
- [x] 4.3 RED/GREEN metric test: the new counter records low-cardinality labels and status.
- [x] 4.4 Migration test: new column exists after upgrade path.

## 5. Validation

- [x] 5.1 `openspec validate upstream-transport-observability --strict`.
- [x] 5.2 Targeted pytest for request logs, smart routing, metrics, migration, and limit warmup protocol compatibility.
- [x] 5.3 `ruff check`, `ruff format --check`, `uv run ty check`, frontend test/typecheck/lint.
