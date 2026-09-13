# Design: Enable the chunked HTTP bridge transcript writer

## Context

The batcher already groups up to 32 events before one repository call. The v1
repository expands that group back into one row per event. The v2 schema and
codec are available from the expand release, but a rollout flag must prevent a
new writer from producing data an older replica cannot read.

## Decisions

### D1. Explicit default-off cutover

Add one canonical setting with values `rows_v1` and `chunks_v2`, defaulting to
`rows_v1`. A new setting is necessary because mixed-version rollout safety
cannot be inferred from the local process. Operators enable v2 only after all
replicas run the dual reader.

### D2. Switch format on first successful v2 append

New operations keep the schema default `rows_v1`. The first v2 append locks the
operation and owner, verifies that logical bytes and both event stores are
empty, then changes the operation to `chunks_v2` in the same transaction as the
first chunk. An operation with any v1 material cannot switch.

### D3. One chunk per batch

Normal batch flush writes one encoded chunk. Sequence numbers continue from the
previous chunk's range. The terminal path drains pending chunks first, then
writes a terminal one-event chunk and operation state in one transaction.
Logical `event_bytes` remains the sum of UTF-8 event bytes, not compressed or
framed bytes, preserving the existing cap. Ownership, logical byte capacity,
and cumulative event count are revalidated by the fenced write. Stateless
checks and an unfenced ownership/cumulative-limit preflight reject obvious
invalid input before the chunk is compressed on a worker thread, while sequence
allocation reads only the latest chunk's sequence metadata rather than its
payload.

### D4. Fail closed on format conflict

A v1 writer cannot append after v2 format selection, and a v2 writer cannot
switch an operation that already has v1 rows. Failure leaves the spool
incomplete and uses the existing settlement/fail-closed path; it never writes
both formats.

## Rollout / Rollback

1. Deploy the dual-reader expand release everywhere.
2. Deploy this release with `rows_v1` unchanged.
3. Enable `chunks_v2` on a canary, then all replicas.
4. Roll back the setting to `rows_v1` before rolling back binaries.
5. Binary rollback must target the dual-reader expand release or newer.
