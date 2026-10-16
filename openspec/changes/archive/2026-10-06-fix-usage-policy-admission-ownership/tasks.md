## 1. Admission boundaries

- [x] 1.1 Fence probe commitment and process-seed persistence; verify policy changes reject dispatch without consuming probe admission or leaking leases.
- [x] 1.2 Reuse cancellation-deferring late selection cleanup; verify repeated cancellation releases stream and token pressure while retaining committed affinity.

## 2. WebSocket rejection

- [x] 2.1 Publish rejected-frame finalization ownership before release awaits; verify cancellation settles leases, API-key reservations, and gates without upstream traffic or duplicate terminal events.

## 3. Verification and delivery

- [x] 3.1 Preserve existing regressions and add product-path scheduling tests; run focused suites and the required local CI gate, recording any environment blockers.
- [x] 3.2 Verify current upstream ancestry, one migration head, and historical upgrade coverage; sync contracts and archive only after verification.
- [x] 3.3 Prepare verification and handoff evidence for PR delivery and monitoring.
