# Add manual account routing policy

Operators need to intentionally spend or preserve specific upstream accounts without pausing them. Temporary or expendable accounts should be consumed before ordinary accounts, while identity-linked or review-critical accounts should be held back until no other eligible account can serve the request.

## Scope

- Add a persisted per-account routing policy with `normal`, `burn_first`, and `preserve`.
- Add per-additional-quota routing policy overrides with `inherit`, `normal`, `burn_first`, and `preserve`.
- Surface the policy in account dashboard APIs and controls.
- Apply the policy after hard eligibility filters and before ordinary budget/usage balancing.

## Non-goals

- Do not bypass model-plan or additional-quota gates.
- Do not override sticky affinity when the sticky account is still eligible.
- Do not create arbitrary per-model policy rules outside the known additional-quota registry.
