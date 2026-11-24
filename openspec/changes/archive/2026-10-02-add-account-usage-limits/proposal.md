# Add per-account usage limits

## Why

Operators can pause an account or give it a soft routing policy, but they cannot share only part of an account's standard Codex quota. Issue #631 asks Codex LB to stop routing an account after a configured share is used so the rest stays available for direct use. Budget pressure and the `preserve` policy are advisory and can fall back to a pressured account, so they cannot provide this guarantee.

## What Changes

- Persist a per-account default maximum-used percentage, optional 5-hour and weekly overrides, and one enabled flag. Disabling keeps the values; removal clears them. Policies default off.
- Add `PUT /api/accounts/{id}/usage-limit` with omit-keeps, null-clears update semantics, and expose the saved policy, effective per-window thresholds, and evaluated state in account summaries.
- Evaluate the policy with one shared evaluator against current standard observations after weekly-only and monthly-only shapes are normalized. A reached window is a hard exclusion applied before every routing strategy, sticky, additional-quota, and fallback path. Missing or stale required data fails closed.
- Re-authorize the pinned owner from committed database state before each new HTTP bridge turn, WebSocket `response.create`, and synthetic warmup dispatch, and return the stable local errors `account_usage_limit_reached` (HTTP 429) and `account_usage_limit_authorization_failed` (HTTP 503).
- Add a reserve editor and show reserved, usable, and consumed quota on account and dashboard views.
- Add the Alembic revisions that introduce the fields and join them with upstream history.

## Non-goals

- Predicting a request's quota cost. Usage is observed after requests, so one request or concurrent in-flight work can move usage past the threshold before Codex LB can block the account.
- Token-precise reservation against the undocumented upstream percentage denominator.
- Limiting additional-quota pools.
