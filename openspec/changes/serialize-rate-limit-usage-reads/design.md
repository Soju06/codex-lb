## Context

`_proxy_repo_context()` creates one background `AsyncSession` and constructs all
members of `ProxyRepositories` with that same session. In both
`_compute_rate_limit_headers()` and `get_rate_limit_payload()`, primary and
secondary reads are started with `asyncio.gather`; both calls reach
`UsageRepository.latest_by_account()` and therefore may execute on the shared
session at the same time.

The PostgreSQL/asyncpg path performs `session.execute()` and does not permit
concurrent operations on one session/connection. The default file-SQLite path
uses a separate synchronous connection in a worker thread, so unit and local
SQLite coverage can pass while production PostgreSQL fails. The project already
serializes the same three-window pattern in load-balancer input loading under
the `query-caching` capability.

## Goals / Non-Goals

**Goals:**

- Ensure one `ProxyRepositories` bundle never starts overlapping usage queries
  from either rate-limit surface.
- Preserve returned headers, payloads, cache behavior, query count, and error
  propagation.
- Add a deterministic regression that fails on scheduling overlap regardless of
  database timing, plus coverage on PostgreSQL.

**Non-Goals:**

- Combine the window reads into a new SQL query or change repository APIs.
- Add sessions, pool capacity, caching, or configuration.
- Change usage refresh, normalization, expiry, additional-limit aggregation, or
  the public `/api/codex/usage` schema.

## Decisions

### Await each shared-session read sequentially

Replace both `asyncio.gather` calls with ordered awaits for primary and
secondary rows; retain the existing monthly and credit/additional-limit reads in
their current order. Remove the now-unused `asyncio` import.

Alternative considered: create one session per concurrent window read. Rejected
because it increases connection-pool pressure, weakens snapshot consistency,
and adds lifecycle complexity for negligible latency benefit from short local
queries.

Alternative considered: introduce one batched repository query. Rejected for
this correctness fix because it changes query shape and result assembly beyond
what is needed; it can be evaluated separately with profiling evidence.

### Test scheduling, not incidental driver behavior

Add a usage-repository double with an in-flight sentinel. Each
`latest_by_account()` call yields once while marked active and fails if another
call enters before it completes. Exercise both `_compute_rate_limit_headers()`
and `get_rate_limit_payload()` through a repository bundle whose members model
the shared-session ownership contract.

The deterministic unit test proves serialization even on SQLite-based test
runs. A focused PostgreSQL integration then proves that the real public rate
limit header path and `/api/codex/usage` payload remain valid on the backend that
exposed the risk.

## Risks / Trade-offs

- **Two independent reads no longer overlap** → The queries are local and short;
  correctness on asyncpg outweighs their small theoretical overlap. Preserve
  query count and measure separately before considering batching.
- **A weak mock passes without modeling session ownership** → Use an explicit
  overlap sentinel with an `await` yield, not only call-order assertions.
- **Payload/header content drifts while arranging tests** → Assert representative
  primary/secondary/monthly values and credit metadata before and after the
  scheduling change.

## Migration Plan

1. Add the deterministic overlapping-operation regression and confirm it fails
   against the current `asyncio.gather` implementation.
2. Replace both gather calls with sequential awaits and remove the unused
   import.
3. Run focused unit and PostgreSQL integration coverage, followed by lint,
   typing, and strict OpenSpec validation.

Rollback is a code-only revert with no persisted-state or deployment migration.

## Open Questions

- None.
