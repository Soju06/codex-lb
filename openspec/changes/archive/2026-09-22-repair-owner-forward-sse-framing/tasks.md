## 1. Implementation and verification

- [x] 1.1 Add bridge-client framing and malformed UTF-8 regressions; demonstrate failure before the fix.
- [x] 1.2 Reuse canonical separator detection with incremental buffering and replacement decoding; pass chunk-boundary and EOF cases.
- [x] 1.3 Run forwarding and canonical SSE suites, type/lint checks, and strict OpenSpec validation; verify timeout ownership and document inherited gate failures.
