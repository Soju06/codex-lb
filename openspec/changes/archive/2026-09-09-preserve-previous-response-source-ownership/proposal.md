## Why

`previous_response_id` syntax does not identify the system that owns a response: OpenAI-compatible model sources may emit the same canonical `resp_<hex>` shape as the subscription backend. Routing by that shape can move a valid source continuation onto a subscription account and can make the direct WebSocket path bypass its required HTTP fallback.

## What Changes

- Use recorded subscription continuity ownership, rather than an ID regex, when a prior response must override configured model-source routing.
- Keep source-configured models source-routed when no subscription owner is known, regardless of the provider's response-ID format.
- Apply the same ownership decision to HTTP Responses routes and the direct Responses WebSocket source guard.
- Preserve the existing subscription-only exclusions for Codex compaction and account-pinned file references.
- Add HTTP and direct WebSocket regressions for subscription-owned and source-owned prior responses.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Define owner-evidence-based prior-response routing across HTTP and direct WebSocket transports.

## Impact

Affected areas are Responses source selection, direct WebSocket model-source fallback, the shared source-route exclusion policy, Responses compatibility requirements, and focused routing tests. No schema, migration, setting, or public request/response shape changes are required.
