## 1. Specification

- [x] 1.1 Define traceback privacy and line-scoped structure requirements.
- [x] 1.2 Record the per-line decision, rejected alternatives, and known pre-existing policy limits in `context.md`.

## 2. Regression proof

- [x] 2.1 Add failing text and JSON formatter coverage for secret-bearing tracebacks.
- [x] 2.2 Add failing coverage for line-terminator boundaries, chained exceptions, JSON values cut by a line end, and cached `exc_text`.
- [x] 2.3 Capture the targeted RED output before production edits.

## 3. Implementation

- [x] 3.1 Reuse the shipped secret patterns per traceback line without collapsing whitespace.
- [x] 3.2 Redact `formatException()` output in the text and JSON application formatters.
- [x] 3.3 Redact or regenerate traceback text cached on a shared record without mutating it.

## 4. Verification

- [x] 4.1 Capture the targeted GREEN output.
- [x] 4.2 Run Ruff, Ruff format, ty, and the logging, CLI, and OTel unit tests.
- [x] 4.3 Validate this OpenSpec change strictly.
- [x] 4.4 Run real handler QA for text and JSON outputs.
