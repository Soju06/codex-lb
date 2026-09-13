# Change: Add chunked HTTP bridge transcript storage

## Why

Durable HTTP bridge recovery currently stores one SQLite row, UUID, hash, and
index entry for every SSE block. High-volume streams create millions of rows
per retention window even though the in-memory batcher already groups adjacent
events. The storage layer needs a compact format without weakening restart
recovery or rolling-deployment safety.

## What Changes

- Add an additive `http_bridge_operation_event_chunks` table and an explicit
  `spool_format` discriminator on durable operations.
- Add a deterministic, bounded compressed chunk codec.
- Read both legacy row transcripts and chunk transcripts while keeping all
  production writers on `rows_v1` in this change.
- Make reset, retry, rollback, and retention cleanup understand both storage
  tables.
- Refuse a downgrade that would discard chunk-format transcript data.

## Non-Goals

- Enabling chunk writes or changing the writer default.
- Backfilling legacy event rows.
- Changing transcript retention windows or replay eligibility.
- Dropping the legacy event table or reader.

## Impact

- Affected specs: `responses-api-compat`, `database-migrations`.
- Affected code: DB models/migration, durable bridge codec and repository,
  focused migration and bridge lifecycle tests.
- No public API, dashboard, or environment-variable change.
