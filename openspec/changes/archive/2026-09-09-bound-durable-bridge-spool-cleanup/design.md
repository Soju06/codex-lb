# Design: Bound durable bridge spool cleanup

## Context

`StickySessionCleanupScheduler` currently loops until
`purge_operation_spool()` returns fewer than 500 deleted operations. Each call
owns its own transaction, but a large backlog still chains an unbounded number
of SQLite write transactions inside one leader-gated task. Production evidence
showed leader renewals and dashboard reads timing out while a multi-million-row
event spool was present.

## Decisions

### D1. Fixed internal budgets

Use fixed internal constants rather than new settings: a small repository
batch, a maximum number of batches, and a monotonic wall-clock budget. Tests
may patch these constants. At least one batch is attempted so a slow database
still makes bounded forward progress.

### D2. Resume instead of drain

A short batch proves the eligible backlog is drained. A full final batch when
the count or time budget is exhausted is reported as likely backlog and retried
immediately in another bounded pass only after a successful retention attempt,
so the aggregate catch-up rate is not artificially capped below ingest
throughput. Leader-election skips and failed attempts retain the backlog signal
but use the bounded retry delay. Repository eligibility and owner fencing stay
unchanged, so retries are idempotent across ticks and leaders.
When a failed pass has already committed deletions, its full selected batch is
evidence of remaining backlog and therefore keeps the retry-bearing signal.

### D3. Low-cardinality observability

Record duration, deleted-operation count, run outcome, and a binary
backlog-likely signal. Do not label metrics with operation, session, account,
or model identifiers. Log only aggregate values.

## Risks / Trade-offs

- A very large first backlog takes multiple ticks to drain. The fixed budget is
  deliberately sized to exceed steady-state expiration volume while protecting
  request latency.
- A full final batch is only a backlog hint; it may be exactly the last batch.
  The next tick resolves that ambiguity cheaply.
- One repository batch can still exceed the wall-clock budget. Keeping the
  batch small bounds that unavoidable unit without splitting owner-fenced
  deletion semantics in this change.

## Rollback

This change has no schema or data-format transition. Reverting restores the
previous drain-all scheduler behavior; rows already deleted were eligible under
the unchanged retention contract.
