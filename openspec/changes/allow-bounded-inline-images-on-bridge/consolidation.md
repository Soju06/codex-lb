# Python consolidation plan and provenance

Source: upstream PR2534 head 5071c18d71c770df508153f814fb30eba9d9f202, trial worktree local/image-bridge-trial based on 72630bbfeee9e3b0d9acfb95e2b1e706b8b0188e. Local changes were uncommitted.

Preserve existing app file patches, not wholesale replacement from trial. Eleven
match trial; beyond those, this consolidation intentionally evolved the trial in
five audited ways: (1) proxy_websocket.py received peer1009 classification;
(2) helpers.py 100s confirmation cap; (3) request_submit.py account-neutral
HTTP400 settlement; (4) streaming.py safe connect diagnostics (requires
connect_diagnostics.py); (5) admission extended to WebP alongside JPEG/PNG, so
the proposal, context and responses-api-compat delta all name JPEG/PNG/WebP
while trial PR2534 head admitted only JPEG/PNG. The 100s cap is not a connect
timeout. Ported trial tests were adapted where they encode pre-contract
behavior, never silently: the two eventless-timeout tests now assert the
consolidated no-blind-replay contract (one send attempt, no replacement
connection, duplicate-work warning) because silence on an admitted image send
must not trigger automatic redispatch; the unsupported-media parametrizations
use GIF because WebP is admitted here.

Plan: retain existing behavior; add missing ambiguous-image generic/clean/send warning regression and minimal fix; preserve original normative assertions; local receive overflow reclassification is proposal-only in distinguish-local-websocket-receive-limit. Explicitly stage only reviewed Python/spec/test paths, no directories. Native code, runtime configuration, credentials and production image data excluded. No production service operation or business request.

Current isolated verification: combined new warning routes, three text controls and existing safety tests 78 passed (one AnyIO warning); cap/transport selection 73 passed, 1054 deselected. Historical warning contract reproduced three RED failures before implementation. Full bridge suite is not claimed green: prior cap-only comparison retained the image patch and is not a clean baseline, so its failures must not be labeled pre-existing upstream defects.

Validation before warning change: 63 passed (transport fallback, diagnostics, peer1009 integration), one AnyIO deprecation warning. This is not full image-suite or Native acceptance. OpenSpec CLI unavailable; no installation attempted, no strict CLI validation or archive claimed.

Open: Native paired Python/Rust port/build/helper verification (toolchain unavailable); full production PNG/JPEG/WebP first+retained-image/tool-output continuation after separately authorized coordinated restart, requiring response.completed and correlated websocket transport; session-e5d33019 pre-created retry failure investigation.
