## Implementation

- [x] Reproduce quota failure for a complete WebSocket resend containing response-owned bookkeeping.
- [x] Project only verified, complete resends that pass account-neutral payload validation.
- [x] Cover successful account switching, the following turn, and unsafe resends; run proxy tests, lint, typing, and spec validation.
- [x] Verify and archive the change.

Verification after maintainer review: 2,846 tests passed across the complete WebSocket integration, proxy-utils, proxy-HTTP-bridge, replay-safety, and new projection unit suites. The existing fresh-replay fingerprint regression passes unchanged. Added same-account retry, size-slimming, and unknown-envelope coverage. Lint, architecture checks, typing, contributor attribution, and strict validation of all 65 specs passed. Full local CI stops at frontend installation because Bun is unavailable.
