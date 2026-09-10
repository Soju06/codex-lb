## 1. Contract

- [x] 1.1 Specify opt-in controlled-origin failure scenarios.
- [x] 1.2 Specify privacy-safe transport-error capture and failure-outcome
  reporting without weakening strict parity.

## 2. Implementation

- [x] 2.1 Add 429, 503, timeout, incomplete SSE, WebSocket rejection, and
  incomplete WebSocket scenarios to the controlled origin.
- [x] 2.2 Capture targeted HTTP flow errors with bounded categories and partial
  response metadata.
- [x] 2.3 Add failure-outcome observations to JSON and Markdown reports.
- [x] 2.4 Document the failure-path capture matrix.

## 3. Verification

- [x] 3.1 Add fixture, capture, turn extraction, comparison, and report
  regressions.
- [x] 3.2 Run focused tests, lint/type checks, strict OpenSpec validation, and
  archive only after verification.
