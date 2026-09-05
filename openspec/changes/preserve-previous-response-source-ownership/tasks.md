## 1. Contract

- [x] 1.1 Sync the owner-evidence routing requirement into the main Responses compatibility spec.

## 2. Routing Implementation

- [x] 2.1 Remove response-ID-shape inference from the shared structural source-route exclusion policy.
- [x] 2.2 Make both HTTP Responses routes let recorded subscription ownership veto an otherwise valid model-source candidate.
- [x] 2.3 Apply the same owner-aware decision before direct WebSocket connect and reuse source guards.
- [x] 2.4 Resolve recorded subscription ownership before disabled-source denial, even when enabled-source lookup misses.
- [x] 2.5 Treat physically present blank direct-WebSocket turn-state headers as client input and fail closed on owner miss.
- [x] 2.6 Use TypedDicts for the authenticated forwarding-signature payload shape.
- [x] 2.7 Use shape-based synthesized-marker compatibility for owner-miss fallback while retaining API-key scoping and independent hard-owner checks.
- [x] 2.8 Apply shape-based marker compatibility to compact requests without `previous_response_id`; retain fail-closed handling for blank and non-synthetic client markers.
- [x] 2.9 Preserve the pre-marker v2 signing shape for marker-bearing owner forwards and add an additive marker-proof signature.
- [x] 2.10 Restore the WebSocket missing-authorized-pool warning and original security-error handling after authorized retry exhaustion, preserving account-model rejection and hard-owner contracts.

## 3. Regression Coverage

- [x] 3.1 Update unit coverage for structural source-route exclusions without response-ID syntax classification.
- [x] 3.2 Cover subscription-owned and canonical source-owned prior responses on both HTTP Responses routes.
- [x] 3.3 Add direct WebSocket regressions for subscription-owner routing and canonical source-owner HTTP fallback.
- [x] 3.4 Cover unregistered synthetic-shaped compact turn state with a missing previous-response owner through the sole-candidate compatibility path.
- [x] 3.5 Cover unregistered synthetic-shaped compact turn state without `previous_response_id` through normal compact selection.
- [x] 3.6 Cover marker-bearing file-owner forwards, marker-proof tampering, and marker-free pre-marker signing shapes.
- [x] 3.7 Exercise authorized-pool exhaustion through downstream WebSocket requests, including pre-created failures and strict replay boundaries.

## 4. Verification

- [x] 4.1 Run focused tests, Ruff, ty, and scoped/strict OpenSpec validation; inspect the final diff and worktree status.
- [x] 4.2 Preserve turn-state ownership before owner-miss fallback and fail closed when WebSocket candidate lookup is unavailable.
- [x] 4.3 Validate the no-previous-response compact shape-compatibility boundary and strict OpenSpec contract.
