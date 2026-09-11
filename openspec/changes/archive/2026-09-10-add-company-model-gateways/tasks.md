## Discovery
- [x] Inventory entrypoints and current non-GLM model catalog; classify direct inference versus agent-only APIs.
- [x] Verify TRAE direct inference, tools, continuation and model identity with synthetic requests.
## Implementation
- [x] Add fixed-host local TRAE credential binding and model discovery.
- [x] Translate Responses requests and SSE while preserving tools, reasoning state, errors, cancellation and usage.
- [x] Add management and dashboard source controls with honest quota/usage visibility.
- [x] Configure eligible models and verify through an isolated codex-lb and real Codex client.
## Delivery
- [x] Run scoped regression, type/lint and OpenSpec checks; inspect UI.
- [x] Inspect in-flight requests and client routing, then perform a continuity-preserving or user-coordinated cutover.
- [x] Verify active routing and models, document actual exclusions/limitations, sync and archive verified spec.
