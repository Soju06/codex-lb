# Repair Desktop reset readiness

## Why

The Desktop reset candidate has a divergent migration head and review findings affecting connection availability, conflict responses and relay close frames.

## What Changes

- Reconcile current main and join the migration graph without discarding existing revisions.
- Release inventory database sessions before upstream reset-credit and OAuth I/O.
- Guard destructive migration tests with an explicitly dedicated PostgreSQL test database.
- Return the existing conflict response when durable and helper redemption bindings disagree.
- Normalize empty WebSocket close status and strengthen existing route and serialization checks.

## Impact

The PR includes the relay, identity and usage components needed for Desktop's native Reset action. It works on main without waiting for another PR. No real credit consumption or deployment is part of this verification.
