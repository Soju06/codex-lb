## 1. Implementation

- [x] 1.1 Extract only complete JSON parsing and SSE framing; no Lite/image changes.
- [x] 1.2 Baseline route returned upstream_request_timeout instead of unsupported_value; formatting and real-route regressions pass after the fix.
- [x] 1.3 Update native parity and lifecycle parser-spy tests; HTTP SSE parser remains unchanged.

## 2. Validation

- [x] 2.1 Targeted/SSE/drain: 137 passed; overlapping bridge: 20 passed. Native wire: 6 skipped without helper binary. Ruff/format, typing, architecture/cancellation/timing, strict change and 65 main specs passed.
- [x] 2.2 Independently based on beta.9; only parser code, synthetic regressions, and this change.

## 3. Non-finite follow-up

- [x] 3.1 Drop rejected objects before raw SSE relay and validate native nested floats; verify malformed and non-finite unit/route regressions.
- [x] 3.2 Preserve finite payloads, integer precision and normal recovery after a rejected frame; run lifecycle, formatting and typing checks.
- [x] 3.3 Targeted/SSE/drain: 157 passed; stock error/precreated selection: 7 passed. Native routing/parity selection: 32 passed, 7 helper-dependent wire probes skipped. Ruff/format, typing, architecture/cancellation/timing, strict change and 65 main specs passed.
