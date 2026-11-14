# Local trial tasks: bounded inline images on bridge

> Historical trial ledger copied for provenance on 2026-11-15, NOT current-checkout completion claims. Native source port and trial test counts below refer ONLY to the trial worktree. Native files are excluded from the Python consolidation. Current verification is recorded in consolidation.md; CLI validation is unavailable and this change is not archived.

Based on upstream PR #2534 head 5071c18d71c770df508153f814fb30eba9d9f202, ported onto deployed 72630bbfeee9e3b0d9acfb95e2b1e706b8b0188e. Checkboxes below describe LOCAL verification, not upstream author claims.

## Implementation
- [x] Port image admission, exact complete-frame budgeting, rollback flag, no historical slimming, and 1009 terminal classification.
- [x] Fix oversized inline images plus long history bypassing the explicit 400 budget gate.
- [x] Suppress sent-but-unacknowledged image transport replay while retaining text and accepted-lifecycle retry controls.
- [x] Preserve precreated explicit capacity rejection recovery with current-attempt evidence. Real HTTP regression red502 -> green; stale proof cannot authorize third send after next ambiguous attempt. Final40 image/capacity/1009 tests plus63 admission/native-Python/text controls passed; independent static re-review found no new blocker.
- [x] Provide truthful ambiguous-execution error messaging for image timeouts.
- [x] Port native protocol pair source changes; do not activate an old helper.

## Verification
- [x] Baseline related image tests: 60 passed before implementation.
- [x] Observe stock text-image-text route fail; observe upstream long-history oversized-image regression fail, then 3 related routes pass after fix.
- [x] Observe generic image disconnect replay twice before guard; strengthen exactly-once send-attempt assertion.
- [x] Silent image, clean close, uncertain send and independent recovery targeted tests: 4 passed.
- [x] Related image/admission/1009/native-Python and text controls: 100 passed; later adapter+classification15 passed, then final image/1009 routes38 passed. Capacity-proof follow-up is separate.
- [x] Static lint/type, architecture, settings tiers, cancellation/timing checks; strict local OpenSpec valid and main specs67/67.
- [x] Real DSH screenshot conversion source traced; opt-in controlled upstream trial attempted with isolated state and no desktop execution.
- [x] Actual screenshot upstream acceptance/cache verification: after initial quota failure, fresh usage snapshot pinned a known-available Team account. Three requests completed on one connection;18,647-byte tool-result JPEG accepted; cached3072/4683 then4352/4698, including image-cache attribution1066/1395.
- [ ] Native real-helper integration (not available locally; required before enabling native path).
- [x] Trial handoff and rollback instructions recorded in D:/files/DSH/reports/clb-image-bridge-trial-20261105.md. Python-only acceptance complete; production cutover requires a separate explicit decision. No commit/push/deployment performed.

## Known boundaries
- No host Rust toolchain found; deployed launcher currently uses Python adapters, not native egress.
- Existing reader retirement has a bounded cancellation wait cycle; tests require final socket close within 5s, not an immediate close. No unrelated lifecycle refactor in this trial.
- Synthetic invalid-image responses do not prove the historical production invalid-image shape; controlled real upstream evidence remains necessary.
- 64MiB is a local cap, not an upstream acceptance guarantee.

## WebP approved extension
- [x] Red-green real route tests: transparentWebP/mixedJPEG-PNG-WebP one connection, verbatim bytes, oversize400 before send.
- [x] Add WebP strict-base64 prefix without changing budget/ambiguity/rollback policy; GIF remains unsupported control.
- [x] Final image/admission/capacity/1009 suite89 passed; key lint/type/architecture and strictOpenSpec valid; independent review no new blocker.
- [x] Bounded live transparentWebP trial:3 completed responses,1 connection; cached3072/3294 then3200/3309. No production switch or Native activation.
