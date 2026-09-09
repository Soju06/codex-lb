## Purpose and scope

This change isolates a deterministic local error-envelope bug found while validating the beta.5 candidate. It does not alter beta.5 ownership semantics or weaken the API-key account-assignment boundary.

## Evidence

The broad candidate integration run reported `hard_affinity_saturated` expected versus `upstream_request_timeout` received in `test_codex_goal_restart_cannot_retire_owner_outside_api_key_scope`. The exact test passes alone, while the full sticky-session module reproduces the failure with two workers. A detached beta.4 worktree reproduces the same module result (41 passed, 1 failed), so the issue predates the beta.5 merge.

## Operational note

The fix must be validated with virtual time and a route-level test so it cannot rely on shortening the timeout or accepting either error code. After rollout, monitor local selection error codes and request-budget exhaustion separately.

## Implementation decision

Selection already knows the pre-health authenticated mutation-authority pool.
The fix carries a typed `hard_affinity_scope_mismatch` fact from sticky selection
to the common recovery helper, which declines the impossible capacity wait.
It does not retain historical errors across calls or translate arbitrary
`TimeoutError` exceptions. This narrows the initial design: prevent the
deadline-sensitive retry rather than catch its eventual timeout. In-scope
owners in cooldown, quota exhaustion, or retry exclusions retain recovery.

For example, a raw session owned by A with an API key assigned only to B emits
one hard-affinity terminal after one selection. An in-scope A excluded after a
transient failure still uses the existing same-owner recovery flow. The initial
SSE transport heartbeat is unaffected; only capacity-wait progress is absent.
