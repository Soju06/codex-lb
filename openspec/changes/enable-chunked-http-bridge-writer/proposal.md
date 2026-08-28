# Change: Enable the chunked HTTP bridge transcript writer

## Why

The expand release can read `chunks_v2` but still writes one database row per
SSE block. A guarded writer cutover is required to realize the storage
reduction after every serving replica has the dual reader.

## What Changes

- Add an explicit `rows_v1|chunks_v2` writer setting that defaults to
  `rows_v1` for rollout safety.
- Persist each in-memory event batch as one compressed v2 chunk when enabled.
- Append the terminal event and operation outcome atomically in v2 format.
- Refuse to mix row and chunk material in one operation.

## Non-Goals

- Changing the default writer format in this release.
- Backfilling or deleting legacy event rows.
- Changing replay, retention, byte-cap, or owner-fence semantics.

## Impact

- Affected spec: `responses-api-compat`.
- Affected code: settings, event batcher, durable bridge coordinator and
  repository, focused writer and lifecycle tests.
- No schema migration; this change requires the expand migration first.
