## Context

The streaming retry loop has a generic pre-visible error handler inside the
account-attempt loop and an outer HTTP 401 handler that owns forced refresh.
The inner handler must not classify a 401 for failover or write account health
before the outer handler has refreshed and retried it.

## Decision

Re-raise pre-visible HTTP 401 directly to the existing outer handler. Reuse its
same-account retry, exclusion, deferred keyed health queue, and settlement path.
Keep all non-401 routing and accepted-response no-replay behavior unchanged.

## Verification

The Responses test uses the real route, service, balancer, and database with
only upstream transport and token freshness stubbed. For example, account A
returns `token_revoked`, refresh succeeds, A returns it again, then B completes.
The health-write wrapper verifies committed usage before calling the real write.

WebSocket route cases send `error` and `response.failed` without an auth type.
Pre-created failures reuse one request state across refresh and failover;
ID-bearing failures retain their owning account and are never replayed.
