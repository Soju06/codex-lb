# Why

Complete HTTP bridge transcript recovery can retain durable data across a
redeploy but still fail for older, long-lived sessions. The default 128-turn
walk stops before a persisted replay snapshot, and snapshot lookup currently
discards the newer turns between that snapshot and the requested stale anchor.
Older snapshots also contain response-owned reasoning, metadata, annotations,
and empty tool-output fragments that the current account-neutral validator
correctly rejects.

# What Changes

- Raise the bounded transcript walk default to 256 turns while retaining the
  existing 8 MiB, 4,096-item, API-scope, ownership, and duplicate-suppression
  guards.
- Use a persisted snapshot as the oldest replay root and append the durable
  descendant tail through the requested stale anchor.
- Sanitize only known response-owned legacy bookkeeping before replay
  validation; leave unknown shapes fail-closed.
- Add regression coverage for legacy snapshots and snapshot-plus-descendant
  recovery.

# Capabilities

## Modified Capabilities

- `responses-api-compat`: complete HTTP bridge transcript recovery preserves
  long-session continuity across snapshot boundaries.

# Impact

Recovery remains opt-in and account-neutral. The change only makes already
durable, bounded transcripts eligible when their persisted shape is
unambiguous; it does not re-execute unresolved tool calls or relax the replay
validator for unknown fields.
