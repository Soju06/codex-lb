# Design: Add chunked HTTP bridge transcript storage

## Context

The event batcher persists several SSE blocks in one transaction, but the
repository still inserts one `http_bridge_operation_events` row per block.
Recovery later reads those rows in sequence and requires a complete terminal
spool. Any replacement format must preserve exact strings, duplicate
occurrences, ordering, byte limits, owner fences, and fail-closed behavior.

## Decisions

### D1. Additive versioned storage

Add `http_bridge_operations.spool_format` with non-null default `rows_v1` so
all historical and newly written operations retain current behavior. Add a
chunk table keyed by `(operation_id, first_sequence_number)`. This change adds
dual readers but does not enable v2 writes.

### D2. Chunk framing and integrity

Frame each event as a four-byte big-endian length followed by strict UTF-8,
concatenate the frames, and compress the canonical bytes with zlib. Store the
codec name, event count, uncompressed byte count, compressed payload, and
SHA-256 of the canonical bytes. Decoding is bounded by the existing transcript
byte budget and rejects unknown codecs, oversized output, malformed framing,
invalid UTF-8, hash mismatch, non-contiguous sequence ranges, trailing bytes,
and more than 65,536 events across one operation. The later chunk writer uses
the same operation-wide event-count limit, so the reader treats a larger
persisted value as invalid rather than replaying an unbounded event list.

### D3. Exact fail-closed replay

`rows_v1` uses the existing ordered row query. `chunks_v2` reads chunks in
`first_sequence_number` order, requires contiguous sequence ranges beginning
at one, and returns no events on any decode or integrity failure. Existing
replay eligibility then rejects the incomplete transcript without upstream
redispatch. The replay reader uses the configured operation spool byte limit,
so a valid configured transcript is never rejected by a smaller reader-only
cap.

### D4. Dual-format lifecycle cleanup

All spool reset/retry/retention paths delete both legacy rows and chunks for
the selected operation IDs. Rollback-before-dispatch treats either table as
evidence that dispatch work exists. Operation state and logical `event_bytes`
remain the single replay-size authority.

### D5. No eager backfill

Legacy rows remain readable and expire through normal retention. Avoiding a
multi-million-row rewrite keeps rollout latency bounded and makes the expand
release safe to deploy before the writer cutover.

## Mixed-Version Operation

This release writes only `rows_v1`, so old and new replicas observe the same
data. A later writer release may produce `chunks_v2` only after every replica
can read it. Rollback after v2 activation must target this dual-reader release
or newer.

## Rollback

The migration downgrade removes the additive table and discriminator only when
no chunk rows and no `chunks_v2` operations exist. Otherwise it fails before
destructive DDL. Before writer activation, downgrade is data-preserving.
