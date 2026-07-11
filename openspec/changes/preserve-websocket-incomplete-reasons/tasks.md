## 1. Specification

- [x] 1.1 Define the WebSocket incomplete-reason request-log contract.

## 2. Implementation

- [x] 2.1 Extract a valid incomplete reason from terminal WebSocket payloads.
- [x] 2.2 Record the extracted reason in WebSocket request-log settlement.

## 3. Verification

- [x] 3.1 Add regression coverage for `max_output_tokens` logging and
  non-penalizing incomplete handling.
- [x] 3.2 Run targeted tests, lint, type checks, and OpenSpec validation when
  the CLI is available.
