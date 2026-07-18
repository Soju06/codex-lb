# Implausible Rate-Limit Reset Metadata

## Purpose and scope

This change hardens the existing durable rate-limit cooldown contract against malformed or wrong-unit metadata from the OpenAI service that codex-lb proxies. It does not change normal quota accounting or introduce a manual account-state override.

## Incident shape

A live account was persisted as `rate_limited` with `blocked_at=1784146959` and `reset_at=15023672358`. The latter maps to January 2446, while fresh usage snapshots repeatedly reported `used_percent=0.0`. Because the reset deadline remained in the future, the normal recovery path correctly—but indefinitely—preserved the block.

The exact OpenAI service payload was not retained, so the malformed value may have originated in that service or during a runtime response-normalization layer. The codex-lb application boundary must be safe in either case.

## Decision rationale

Reset metadata is an untrusted hint, not an authorization or billing record. codex-lb should honor plausible explicit metadata for cross-replica correctness but should never let one numeric field disable an account for centuries. A fixed 366-day horizon is intentionally much larger than the currently supported monthly quota window while still rejecting obvious unit/domain errors.

Invalid metadata is not converted heuristically. Treating `15023672358` as milliseconds or nanoseconds would guess at the OpenAI service's intent. Instead, codex-lb uses the already-defined Retry-After/message/backoff chain.

## Constraints and failure modes

- Legitimate weekly and monthly deadlines remain authoritative.
- Missing or rejected metadata still produces a persisted minimum cooldown, so peer replicas do not hammer a throttled account.
- Existing poisoned rows with a block marker recover only after the minimum floor and only from post-block evidence; legacy rows without a marker require recent evidence.
- Recovery requires every applicable primary and long quota window to remain below `100%` usage.
- Compare-and-set persistence prevents stale recovery from overwriting a concurrent new rate-limit event.
- No setting is added: accepting century-scale cooldowns is not a useful operator-tunable behavior.

## Concrete example

At epoch `1784146959`, an error with `resets_at=15023672358` is rejected because it is more than 366 days ahead. If the error message has no parseable duration, the persisted deadline becomes at least 30 seconds after the block. A subsequent fresh usage snapshot showing available quota can then restore the account to `active` after that floor elapses.

## Operational notes

No migration or manual cleanup is required. After deployment, background usage refresh or the next selection evaluation repairs affected rows in place. The normative contracts are in the account-routing and usage-refresh-policy delta specs for this change.
