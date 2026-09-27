## 1. Reproduction and implementation

- [x] 1.1 Add public-route regressions for namespaced retained calls and output-only continuations, proving failure before the fix.
- [x] 1.2 Classify validated client tool-result IDs without dropping call ownership; verify strict shape and unchanged subscription/wire tests.
- [x] 1.3 Verify unavailable/replaced owners, unknown/mixed calls, opaque state, API-key scope and publication guards with route regressions.

## 2. Validation and documentation

- [x] 2.1 Run focused SQLite and PostgreSQL integration suites plus lint/type checks, recording results.
- [x] 2.2 Complete independent adversarial review and resolve actionable findings, then verify the final diff.
- [x] 2.3 Sync normative spec and stable context, run strict OpenSpec validation, and archive the verified change.
