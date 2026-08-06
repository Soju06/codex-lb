# Add per-account usage limits

## Why

Operators can pause an account or give it a soft routing policy, but they cannot share only a bounded fraction of an account's standard Codex quota. Upstream issue #631 asks for exactly this: stop routing an account after a configured percentage is consumed so the remaining quota stays available for direct use. Existing budget and `preserve` behavior is intentionally advisory and may fall back to a pressured account, so it cannot provide this contract.

## What Changes

- Add a persisted per-account maximum-used percentage with an independent enabled flag, so an operator can temporarily disable a limit without forgetting its value or remove it completely.
- Add an account API and dashboard control for setting, toggling, editing, and removing the limit.
- Evaluate the limit against current standard primary and long-window (weekly or monthly) usage. Reaching the percentage in any reported standard window is a hard account-selection exclusion.
- Apply the exclusion before sticky, single-account, routing-policy, additional-quota, health, and fallback selection so no strategy can burn through it.
- Fail closed for an enabled limit when current standard usage data is unavailable, and return a stable routing error instead of treating the account as upstream-rate-limited.
- Add an Alembic migration plus backend, API, migration, and dashboard tests.

## Non-goals

- Predicting the exact quota cost of an upstream request. Upstream usage is observed after requests, so one request or concurrent in-flight work can move the reported percentage past the configured value before Codex LB can observe and block it.
- Token-precise reservation against an undocumented upstream percentage denominator.
- Applying the standard-account limit to separate additional-quota pools.
