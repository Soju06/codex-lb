## Context

See proposal.md for the observed delay. The batcher already serializes writes and takes one bounded batch per operation. Its event wake is cleared before each pass, so a burst leaves pending events without another wake.

## Goals / Non-Goals

Drain existing eligible backlog while preserving bounded transactions and round-robin passes. Keep the first wake and interval policy unchanged. Paced coalescing would change durability lag and is a separate decision.

## Decisions

Rearm the existing wake after a pass if eligible operations still have pending events. Retain the existing wait and per-operation iteration so cancellation and scheduler cooperation remain intact. Draining one operation completely inside its iteration would postpone other operations and is rejected.

Tests call enqueue, terminal append/drain and close through the existing public interface. The injected durable bridge records ordered persistence calls and controls writer completion. No private-method assertions are needed. A long test interval distinguishes backlog progress from a timer expiry.

## Risks / Trade-offs

Continuous backlog issues writes sooner. Batch size and one-batch-per-operation passes still bound each transaction and preserve fairness. A blocked writer remains governed by existing cancellation and terminal behavior. This change does not claim to eliminate provider latency or SQLite contention.

## Migration Plan

No schema migration or configuration change. Deploy by the normal release process and roll back by restoring the prior application revision. Live deployment remains outside this worker's authority.
