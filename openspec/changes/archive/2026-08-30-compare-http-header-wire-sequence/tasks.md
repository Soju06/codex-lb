## 1. Regression

- [x] 1.1 Add addon tests proving order, duplicates, and casing are retained
  without values.
- [x] 1.2 Add analyzer/report tests for match, casing-only mismatch,
  order/duplicate mismatch, and missing evidence.

## 2. Implementation

- [x] 2.1 Record privacy-safe request header sequence metadata for HTTP and
  WebSocket capture records.
- [x] 2.2 Add independent A/C header-name order and casing comparisons and
  render them in the server-observable report.
- [x] 2.3 Update the runbook with the evidence boundary and remaining
  SETTINGS/HPACK limitation.

## 3. Verification

- [x] 3.1 Run focused tests, formatting, lint, type checks, strict OpenSpec
  validation, and diff integrity checks.
