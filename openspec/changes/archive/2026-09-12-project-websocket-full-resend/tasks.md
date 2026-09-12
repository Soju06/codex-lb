## Implementation

- [x] Reproduce quota failure for a complete WebSocket resend containing response-owned bookkeeping.
- [x] Project only verified, complete resends that pass account-neutral payload validation.
- [x] Cover successful account switching, the following turn, and unsafe resends; run proxy tests, lint, typing, and spec validation.
- [x] Verify and archive the change.

Verification: 459 WebSocket and replay-safety tests plus 4 existing ownership/replay tests passed. Lint, architecture checks, typing, and strict OpenSpec validation passed. Full local CI stops at frontend installation because Bun is unavailable.
