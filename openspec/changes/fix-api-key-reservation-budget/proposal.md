## Why

One API key can return proxy-side 429s when several Codex lanes start at the same time even though the completed requests would be within the key's configured usage limits. The local API-key admission path currently reserves a fixed 8192 input + 8192 output token budget for every in-flight request, and prices `cost_usd` reservations from that budget. With `enforced_service_tier=priority`, a single `gpt-5.5` reservation is about $0.716799, so eight concurrent lanes require about $5.73 of temporary cost headroom before any request has actually completed.

This makes usage limits double as an implicit concurrency limit and causes false 429s for normal multi-lane Codex operation.

## What Changes

- Add request-usage reservation budgets to API-key limit enforcement so admission can reserve a bounded estimate instead of the old unconditional 8192/8192 token pair.
- Use a lower bounded default output-token reservation while preserving the existing 8192-token ceiling for strict/opaque inputs.
- Estimate request input budget from the serialized request shape when the payload is self-contained; fall back to the conservative input default for previous-response/conversation/file/image references whose true upstream input is opaque to the proxy.
- Do not trust client-provided output caps that codex-lb does not currently enforce upstream; actual output is still charged during final settlement.
- Keep final accounting unchanged: successful requests settle every applicable reservation item to authoritative usage and service-tier pricing; failed/early-exit requests release the reservation.

## Capabilities

### Modified Capabilities

- `api-keys`: API-key usage reservation admission MUST reserve from a request-aware budget and MUST NOT require `concurrent_lanes * 8192 output tokens` of temporary headroom.

## Impact

- **Code**: `app/modules/api_keys/service.py`, proxy request-limit call sites, and a small proxy usage-budget helper.
- **Tests**: unit coverage for eight concurrent priority lanes under a $5 cost limit, unsupported output-cap handling, zero-reservation finalization, and opaque-input fallback.
- **Schema/API**: no database migration and no public API-field changes.
- **Operational**: priority service-tier enforcement still affects actual cost accounting; the admission reservation is now a bounded estimate rather than a worst-case 16k-token pre-charge.
