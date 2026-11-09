## Why
One API key should support ordinary and Daybreak requests without custom client markers (Soju06/codex-lb#2703).

## What Changes
Add a persisted per-key toggle, default off, with API/cache and dashboard support. Recognize legacy/enforced Blue models and native Blue selectors across Responses WebSocket, HTTP Responses and compact. Reuse existing eligibility, key scope, ownership and retry rules; preserve native fields and reject invalid selectors.

Restrictions follow the DBB conversation and direct continuations. Ordinary parents, siblings and fresh conversations retain normal routing. Required HTTP traffic uses direct subscriptions; DBB affinity does not rewrite ordinary process preferences.

## Capabilities
- `api-keys`: toggle lifecycle, migration, cache and dashboard.
- `responses-api-compat`: intent recognition, forwarding and socket transitions.
- `account-routing`: constrained eligibility and isolated affinity.
- `sticky-session-operations`: conversation-scoped durable restrictions.

## Impact
One Boolean column and migration; existing routing primitives, tests and client docs. No dependency changes or instance-wide settings. Native-field delivery by custom providers (#2580) and Daybreak Red support remain separate.
