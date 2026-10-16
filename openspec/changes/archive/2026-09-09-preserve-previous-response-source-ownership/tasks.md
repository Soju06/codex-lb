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
- [x] 3.5 Cover unregistered synthetic-shaped compact turn state without `previous_response_id` through sole-candidate compact selection.
- [x] 3.6 Cover marker-bearing file-owner forwards, marker-proof tampering, and marker-free pre-marker signing shapes.
- [x] 3.7 Exercise authorized-pool exhaustion through downstream WebSocket requests, including pre-created failures and strict replay boundaries.

## 4. Verification

- [x] 4.1 Run focused tests, Ruff, ty, and scoped/strict OpenSpec validation; inspect the final diff and worktree status.
- [x] 4.2 Preserve turn-state ownership before owner-miss fallback and fail closed when WebSocket candidate lookup is unavailable.
- [x] 4.3 Validate the no-previous-response compact shape-compatibility boundary and strict OpenSpec contract.

## 5. Current-head review corrections

- [x] 5.1 Resolve registered synthetic-shaped HTTP turn-state owners before caching a lookup miss, including disabled-source probes without a previous response.
- [x] 5.2 Apply shape-based owner-miss compatibility in the HTTP bridge while preserving blank, non-synthetic, file, and durable-owner constraints.
- [x] 5.3 Enforce API-key-scoped sole-candidate selection for marker-only compact owner misses, including zero and multiple candidates.
- [x] 5.4 Render disabled-source owner-lookup failures through both HTTP routes' sanitized 502 error envelopes.
- [ ] 5.5 Prove all four corrections at the affected routes, run isolated-database regressions and required checks, and obtain current-head review and hosted results.

## 6. Direct WebSocket registered-owner correction

- [x] 6.1 Reproduce registered marker ownership with source-overlapping models through initial and reused direct WebSocket routes.
- [x] 6.2 Preserve resolved turn-state ownership before both source guards and synchronize the explicit contract without relaxing independent owner constraints.
- [ ] 6.3 Verify affected routes and safeguards, obtain independent reviews, and deliver the corrected head with hosted and CodeRabbit evidence.

## 7. WebSocket capability and owner-cardinality separation

- [x] 7.1 Reproduce the scoped unknown-owner continuation with mixed security authorization through both direct WebSocket routes.
- [x] 7.2 Count possible owners before security-capability filtering while preserving subsequent authorization and synchronize the contract.
- [ ] 7.3 Verify scoped regressions and current-upstream composition, obtain independent reviews, and deliver current-head CI, CodeRabbit, and hosted Codex clearance.

## 8. HTTP bridge authorized-pool exhaustion advisory

- [x] 8.1 Reproduce the missing advisory through HTTP bridge requests when the last authorized replacement is excluded after refresh or authentication failure.
- [x] 8.2 Map authorized-pool exhaustion to the existing missing-pool warning while preserving the original security denial and replay guards.
- [ ] 8.3 Verify affected routes and cleanup, obtain independent reviews, and deliver current-head hosted CI and review clearance.

## 9. Separate owner-miss cardinality from routing eligibility

- [x] 9.1 Reproduce model-catalog narrowing through affected external routes and compare the ownership contract with the separate conversation-scoping contract.
- [x] 9.2 Give all previous-response and marker owner-miss callers one account-assignment-scoped enumeration interface without routing-filter inputs; retain normal selection and conversation semantics.
- [ ] 9.3 Verify catalog, service-tier, scope, unavailable-account, and replay controls; obtain independent review and current-head hosted clearance.
