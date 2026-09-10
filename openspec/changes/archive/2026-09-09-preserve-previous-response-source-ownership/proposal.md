## Why

`previous_response_id` syntax does not identify the system that owns a response: OpenAI-compatible model sources may emit the same canonical `resp_<hex>` shape as the subscription backend. Routing by that shape can move a valid source continuation onto a subscription account and can make the direct WebSocket path bypass its required HTTP fallback.

## What Changes

- Use recorded subscription continuity ownership, rather than an ID regex, when a prior response must override configured model-source routing.
- If the source catalog confirms source ownership and no subscription owner is recorded, keep the request source-routed regardless of the provider's response-ID format.
- For a known subscription model with an owner miss, preserve the compatibility fallback only when exactly one eligible subscription account remains after API-key assignment scoping; fail closed for zero or multiple candidates.
- Treat proxy-shaped `turn_*` and `http_turn_*` values as compatibility placeholders even when their local alias or exact issuance provenance is unavailable; keep blank and non-synthetic client markers hard.
- Apply the same ownership decision to HTTP Responses routes and the direct Responses WebSocket source guard.
- Preserve strict file-pinned/account-owned routing, the subscription-only Codex compaction boundary, and the compact sole-candidate fallback.
- Keep source-catalog unavailability distinct from an owner miss so direct WebSocket requests retain their existing subscription fallback.
- Settle compact API-key reservations before owner-miss diagnostics, health writes, or exit, and add HTTP and direct WebSocket regressions for the external contract.
- Restore the existing WebSocket missing-authorized-pool warning and original security error after an authorized retry exhausts its pool. Preserve account-model rejection fallback and hard-owner replay guards.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Define owner-evidence-based prior-response routing across HTTP and direct WebSocket transports.

## Impact

Affected areas are Responses source selection, direct WebSocket model-source fallback, the shared source-route exclusion policy, Responses compatibility requirements, and focused routing tests. No schema, migration, setting, or public request/response shape changes are required.
