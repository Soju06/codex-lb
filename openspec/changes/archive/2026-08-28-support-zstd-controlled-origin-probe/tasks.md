## 1. Specification

- [x] 1.1 Specify bounded zstd request decoding for the controlled origin.

## 2. Implementation

- [x] 2.1 Decode valid zstd request bodies before JSON parsing.
- [x] 2.2 Reject oversized decoded bodies, malformed zstd, and unsupported encodings.

## 3. Verification and documentation

- [x] 3.1 Add regression tests for valid and invalid encoded requests.
- [x] 3.2 Update the traffic-parity runbook with the Codex zstd behavior.
- [x] 3.3 Run focused tests, lint/type checks, and strict OpenSpec validation.
