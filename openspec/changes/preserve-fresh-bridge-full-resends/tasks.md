## 1. Define the fresh-bridge contract

- [x] 1.1 Specify that a verified client-unanchored full resend with retained completed output or a self-contained direct tool loop remains unanchored on the durable owner when no reusable bridge exists.
- [x] 1.2 Keep incremental continuations, client-supplied anchors, owner-unavailable handling, and account-neutral replay out of scope.

## 2. Preserve the original request

- [x] 2.1 Skip durable anchor injection only for a verified full resend with safe retained context while retaining hard affinity and the durable preferred owner.
- [x] 2.2 Prevent session-level anchor re-injection before the first request on the newly created bridge.
- [x] 2.3 Emit a structured event for the preserved fresh full resend.
- [x] 2.4 Persist the complete prior-response tool-call manifest atomically with the durable response alias only after added/done lifecycle reconciliation, and leave legacy or incomplete rows fail-closed.

## 3. Regression coverage

- [x] 3.1 Update focused unit coverage to assert one unanchored full-resend preparation on the durable owner.
- [x] 3.2 Add public `/v1/responses` coverage for a completed bridge followed by a full resend on a fresh upstream WebSocket.
- [x] 3.3 Cover exact manifest settlement, omitted parallel calls, unsupported mixed client-settled calls, stored-prefix call-ID reuse, lifecycle duplicates, response-ID mismatch, persistence round-trip, account-change clearing, and unsafe owner-forward fallback.

## 4. Verification and handoff

- [x] 4.1 Run focused unit and HTTP bridge integration tests.
- [x] 4.2 Run Ruff, formatting, changed-file type checks, proxy architecture checks, migration checks, and OpenSpec validation.
  - Changed paths pass Ty. Full Ty reports only five existing Windows/POSIX diagnostics for `os.killpg`, `signal.SIGKILL`, and `os.fork` in untouched smoke and refresh-claim tests.
- [ ] 4.3 Open a focused upstream PR linked to the production issue and wait for current-head required CI before requesting Codex review.
