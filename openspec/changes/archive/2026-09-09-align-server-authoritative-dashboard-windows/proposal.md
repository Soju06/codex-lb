## Why

The conversations table and dashboard overview metrics describe different windows whenever the browser clock drifts from the server clock, because the conversations list resolves its `since` window on the client via `Date.now()` while the overview resolves it on the server via `utcnow()`. This violates the membership agreement required by `openspec/specs/conversations-api/spec.md` (a conversation counted by dashboard trends can be missing from the table, and vice versa). A second symptom falls out of the same root cause: because the client recomputes a fresh `Date.now()` timestamp on every 30-second poll, the backend grouped-count cache at `RequestLogsRepository.list_conversations()` (keyed by the exact `(search, since)` tuple) never hits on the polling path and fills with one-off entries, repeating the expensive count query every cycle.

## What Changes

- `GET /api/conversations` accepts an optional `timeframe=1d|7d|30d` query parameter; when supplied, the server derives `since` from its own UTC clock using the same timeframe configuration as the dashboard overview aggregation, so the two views describe the same window.
- `timeframe` and `since` are mutually exclusive on `/api/conversations`: supplying both is rejected rather than silently choosing precedence. Existing `since` callers continue to work unchanged (legacy escape hatch).
- The grouped-count cache key for `list_conversations()` becomes a namespaced semantic identity keyed by `(mode, normalizedSearch, timeframe|normalizedSince)` so equivalent timeframe polls reuse the cached total instead of recomputing on every refetch.
- The frontend conversations hook sends `timeframe` (which it already reads from URL params) instead of synthesizing a browser-clock `since`; the redundant per-refetch `timeframeToSinceIso` call is removed.
- The existing client-side `since` computation path (`timeframeToSinceIso`) and the `use-request-logs.ts` hook are explicitly out of scope for this change.

## Capabilities

### New Capabilities
<!-- None. This change modifies existing capabilities; it does not introduce a new one. -->

### Modified Capabilities
- `conversations-api`: add server-authoritative `timeframe` parameter to `/api/conversations`, define mutual-exclusion with `since`, and require the server-derived window to match the dashboard overview aggregation for the same timeframe.
- `frontend-architecture`: require the dashboard conversations query to send `timeframe` (never a browser-generated `since`) and keep its API parameters stable across automatic refetches for an unchanged timeframe.

## Impact

- **Backend API**: `GET /api/conversations` gains one optional query parameter (`timeframe`) and one new validation rule (mutual exclusion with `since`). No response schema change, no new endpoint.
- **Backend service/repository**: a shared dashboard timeframe resolver is centralized (so overview and conversations compute `since` identically); `list_conversations()` cache key construction changes to a namespaced semantic identity.
- **Frontend API client + hook**: `getConversations` gains a `timeframe` field; `useConversations` stops calling `timeframeToSinceIso` on the refetch path.
- **OpenSpec**: deltas to `conversations-api` and `frontend-architecture`; rationale, cache identity, independent-poll limitations, and rollout order captured in `design.md`.
- **Rollout**: backend must ship before frontend. An older backend that ignores the new `timeframe` param would fall back to the 30-day default window for polling, so the frontend change depends on the backend change being live.
- **Out of scope**: `use-request-logs.ts` (same latent pattern, separate follow-up), response-schema changes, README/changelog.
