## Why

Production `10.0.0.113` hit Postgres backend OOM kills while the dashboard and request selector ran large window-ranking queries over `request_logs` and `additional_usage_history`. The resulting Postgres recovery closed in-flight asyncpg connections and surfaced as dashboard/API 500s.

Temporary production mitigations reduced blast radius, but the durable fix needs the hot-path query contracts and supporting indexes captured in OpenSpec before the workaround is retired.

## What Changes

- Replace additional-quota latest-row lookup window ranking with an index-friendly latest-row shape.
- Constrain selector hot-path additional-quota lookups by canonical `quota_key`, `window`, and candidate account IDs.
- Replace account request-usage summary window ranking with grouped latest-id dedupe before aggregation.
- Add idempotent hot-path indexes for `additional_usage_history` latest lookups and `request_logs` dashboard/account aggregates.
- Document the production failure mode and durable mitigation in query-caching context.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `query-caching`: Defines hot-path query-shape constraints and idempotent index requirements for selector/dashboard aggregate reads.

## Impact

- `app/modules/usage/repository.py`
- `app/modules/accounts/repository.py`
- `app/db/models.py`
- `app/db/alembic/versions/*`
- Integration regression coverage for emitted SQL shape and aggregation semantics
