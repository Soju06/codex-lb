## 1. Selection/retry implementation

- [x] 1.1 Trace the shared streaming, HTTP bridge, and WebSocket selection deadline paths and identify the first deterministic hard-affinity failure to preserve.
- [x] 1.2 Carry typed authenticated-scope mismatch evidence to the shared recovery helper and skip impossible recovery waits without changing generic timeout classification.
- [x] 1.3 Verify lease release, no upstream dispatch, and durable owner preservation on every terminal branch.

## 2. Regression coverage

- [x] 2.1 Add virtual-clock coverage proving a scope-mismatched selection schedules no wait even near the deadline, with in-scope recovery controls.
- [x] 2.2 Add the API-key-scoped public Responses route regression and assert the exact error envelope and durable owner row.
- [x] 2.3 Add/retain positive controls for genuine upstream timeout and recoverable local capacity waits.

## 3. Verification

- [x] 3.1 Run focused proxy tests, sticky-session integration, unit/simulation, lint, type, architecture/timing/cancellation checks.
- [x] 3.2 Run strict OpenSpec validation and record baseline comparisons and residual risks in verification.md.
- [x] 3.3 Verify the change and archive only after all gates pass.
