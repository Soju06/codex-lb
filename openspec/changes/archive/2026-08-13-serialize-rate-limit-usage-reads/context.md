# Context: serializing rate-limit usage reads

## Purpose and scope

This change applies an existing session-ownership invariant to two rate-limit
paths that were missed when load-balancer reads were repaired. Normative
requirements live in
[`specs/query-caching/spec.md`](./specs/query-caching/spec.md).

## Decisions and constraints

One `ProxyRepositories` context means one `AsyncSession`, even though it exposes
several repository objects. Calls through those repositories may be sequenced,
but they may not overlap. The fix deliberately keeps the same queries and
result assembly instead of trading a small correctness patch for a new query
optimization.

## Failure modes

On PostgreSQL, overlapping `session.execute()` calls can surface as an asyncpg
operation-in-progress/session-state error and turn an otherwise healthy proxy
request or usage poll into a 5xx. SQLite can hide the bug because its latest-row
read takes a separate worker-thread connection.

## Concrete example

For rate-limit header construction, one repository context observes this order:

```text
accounts -> primary -> secondary -> monthly -> credits
```

No second usage call starts before the preceding call has returned. The same
rule applies to the corresponding reads used by `/api/codex/usage`.

## Operational notes

There is no setting or rollout toggle. Monitor existing proxy error telemetry
for PostgreSQL session/concurrent-operation errors after deployment; header and
payload values should be byte-for-byte compatible for equivalent database
state.
