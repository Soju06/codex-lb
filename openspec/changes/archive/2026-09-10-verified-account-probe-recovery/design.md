## Context

See proposal.md. Existing status CAS checks integer timestamps but has no rejection generation or request scope. Existing probe transport returns at headers. Advisory health settlement captures its version after dispatch.

## Goals / Non-Goals

Provide explicit verified recovery through the existing operator endpoint. Keep historical holds conservative. Do not add automatic traffic, weaken ordinary recovery rules, or alter owner bindings.

## Decisions

Persist generation and rejected model/tier with the hold, atomically at rejection settlement. Increment generation even for identical timestamps. Unknown scope is explicit through a missing model and never inferred from request logs. Request scope must travel with deferred health settlement, not ambient task context.

Consume the bounded probe stream and recognize completed execution separately from HTTP status. Capture an immutable pre-dispatch snapshot. Use durable CAS for recovery and invalidate selection state only when it lands. Preserve the same account identity across credential refresh and recovery. Singleflight admission must not share request-scoped database sessions across callers.

## Risks / Trade-offs

Historical rows remain unavailable through this new recovery path until an ordinary recovery or a new scoped rejection. Mixed-version writers cannot provide generation guarantees, so deployment must replace all rejection writers before enabling reliance on this path.

## Migration Plan

Add nullable scope and a non-null generation initialized to zero. Historical rows retain their status and deadlines. Verify upgrade/downgrade/upgrade and a single Alembic head. No live migration is part of implementation.
