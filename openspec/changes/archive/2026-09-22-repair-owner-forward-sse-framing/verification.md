# Verification

All three tasks and all three specification scenarios are covered. The bridge
uses the canonical separator helper and overlap cursor; its scheduler waits,
request budget, and error classification remain unchanged.

- Before the fix, 25 new framing/decoding cases failed and six LF-compatible
  baseline cases passed.
- Forwarding and SSE utility suites: 142 passed, including the existing virtual
  scheduler timeout/cleanup test.
- Canonical proxy utilities and native SSE fixtures: 1,426 passed.
- Full Ruff lint/format and `ty check`: passed.
- Strict OpenSpec change validation: passed.
- `make lint` passed architecture, cancellation, timing and settings checks,
  then failed on main's existing migration fork and timestamp collision.

The tests exercise the public bridge client with byte-at-a-time, seven-byte,
and coalesced reads, assert delivery before EOF, parse the terminal event
independently, and cover replacement decoding at EOF. No missing requirements,
uncovered scenarios, or design deviations were found. The full repository CI
gate remains blocked by the separate migration defect tracked in upstream #2461.
