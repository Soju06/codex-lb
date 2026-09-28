## Implementation

- [x] Reproduce the route-level quota failure with durable turn-state ownership.
- [x] Keep the owner's first attempt and its session and turn-state aliases intact.
- [x] Verify the durable full resend lazily, only on quota evidence.
- [x] Route every verified owner-move site through one predicate and one replay state.
- [x] Cover HTTP 429, `response.failed` frames, and quota-caused selection-time owner loss.
- [x] Replay the bridge's account-neutral projection; strip aliases only for the replacement.
- [x] Keep non-quota, post-visible, anchored, file, and unproven requests pinned.
- [x] Run focused tests, lint, type checking, and strict OpenSpec validation.
