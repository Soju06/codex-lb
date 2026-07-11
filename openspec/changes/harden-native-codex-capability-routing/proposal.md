# Harden native Codex capability routing

## Why

The shared Codex model catalog can advertise a model or Fast tier when only a
subset of pooled accounts actually exposes that capability. Routing by plan
alone can then select an account that rejects the model, and a long-lived
downstream WebSocket can keep using an account after it is paused or becomes
ineligible. In addition, the selected account installation identity must stay
consistent across the canonical Codex metadata carriers.

## What changes

- Preserve the union of account-specific model catalogs for picker visibility,
  while retaining an account-level capability index for request selection.
- Fail closed when an authoritative refreshed catalog has no account for the
  requested model or service tier.
- Revalidate an open direct-WebSocket account before each unsent
  `response.create` turn. Fresh requests and verified self-contained full
  resends may reconnect on another eligible account after quota failure; short
  `previous_response_id` deltas remain pinned and fail with a retryable
  continuity error when their owner is unavailable.
- Apply the same replay-safety boundary to direct WebSocket, HTTP bridge, and
  HTTP streaming/image-bypass paths, and remember the account that created a
  successful HTTP-stream response for later continuity lookup.
- Rewrite both `x-codex-installation-id` and an existing
  `x-codex-turn-metadata.installation_id` to the selected account identity on
  HTTP, HTTP-to-WebSocket, retry, and direct-WebSocket paths.
- Remove stale model capability data when accounts are paused or no active
  account remains.

## Impact

- Model registry and refresh scheduling.
- Account selection and HTTP bridge session reuse.
- Direct Responses WebSocket lifecycle and continuity behavior.
- HTTP bridge and HTTP streaming failover behavior.
- Codex installation metadata normalization.
- No schema migration and no credential-format change.
