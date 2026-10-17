## Context

`_UsageRefreshSingleflight` owns one task per account and may expose that task
to many request and scheduler waiters. Its two shared waits still use
`asyncio.shield`, unlike the bridge and token-refresh sites converted by
[`harden-shared-future-admission-waits`](../harden-shared-future-admission-waits/).
The existing helper already provides the required result, exception, and
cancellation semantics; see the
[`proxy-admission-control`](specs/proxy-admission-control/spec.md) delta and
the existing
[`proxy-runtime-observability`](../../specs/proxy-runtime-observability/)
signals.

## Goals / Non-Goals

**Goals:**

- Make both joining and non-joining usage-refresh waits constant-cost under
  cancellation storms.
- Preserve singleflight task ownership and successor ordering.
- Prove the usage-refresh surface, not only the generic helper, maintains one
  fan-out callback.

**Non-Goals:**

- Change refresh selection, persistence, exception swallowing, or shutdown.
- Rewrite the helper or convert request-owned cleanup shields.
- Integrate or rebase the unrelated session-ownership work in PR #1887.

## Decisions

### Reuse the established shared-future helper at both wait sites

Both the `join_existing=True` return path and the `join_existing=False`
predecessor wait can accumulate many waiters on one task, so both call
`wait_on_shared_future`. Reusing the established helper preserves waiter
cancellation isolation while keeping one fan-out callback. Keeping
`asyncio.shield` at either site would retain the incident mechanism; a second
usage-specific helper would duplicate the existing contract.

### Preserve the current control flow

The non-joining path continues swallowing predecessor failures and retries the
loop, while caller cancellation continues propagating. The final wait
continues propagating the selected task's result or exception. This limits the
fix to waiter mechanics and avoids changing refresh policy.

### Test callback structure through the usage singleflight

The regression test attaches many `run` callers, inspects the in-flight task's
callback count, cancels most callers, and verifies the count remains bounded
while a survivor receives the factory result. This test fails with the old
shield implementation because each waiter attaches callbacks to the shared
task.

## Risks / Trade-offs

- **Risk:** The private callback-list assertion depends on CPython asyncio
  internals. **Mitigation:** Match the existing helper and bridge regression
  pattern, and guard only the structural property involved in the production
  incident.
- **Risk:** PR #1887 also edits the same singleflight. **Mitigation:** Keep this
  patch focused on current `main` and call out the conflict so that PR must
  carry this conversion forward.

## Migration Plan

Deploy through the normal image release process after merge. Rollback is a
code rollback; there are no data, schema, or configuration migrations.
