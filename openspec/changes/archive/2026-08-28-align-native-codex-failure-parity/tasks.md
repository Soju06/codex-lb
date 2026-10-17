## 1. Contract

- [x] 1.1 Specify native Codex HTTP fallback and failure-lifecycle parity.
- [x] 1.2 Specify safe `Retry-After` propagation and A/B failure reporting.

## 2. Implementation

- [x] 2.1 Pin native Codex downstream HTTP to upstream HTTP after explicit
  transport and mandatory bypass precedence.
- [x] 2.2 Preserve upstream `Retry-After` on propagated HTTP failures.
- [x] 2.3 Preserve native Codex timeout/truncated-stream termination while
  keeping non-native terminal shaping.
- [x] 2.4 Add A/B failure outcomes and strict retry-hint comparison.

## 3. Verification

- [x] 3.1 Add transport, response-header, lifecycle, analyzer, and report tests.
- [x] 3.2 Run focused tests, lint/type checks, strict OpenSpec validation, and
  archive only after verification.
