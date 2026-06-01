## 1. Implementation

- [x] 1.1 Add relative-availability scoring and selection to the canonical balancer.
- [x] 1.2 Thread relative-availability tuning through proxy and sticky fallback selection.
- [x] 1.3 Persist and expose dashboard settings for the strategy, power, and top-K cutoff.
- [x] 1.4 Add dashboard controls and validation for the new strategy.
- [x] 1.5 Keep hot-path selection logs on stable account IDs instead of raw emails.

## 2. Verification

- [x] 2.1 Add unit coverage for relative-availability selection behavior.
- [x] 2.2 Add unit coverage for sticky fallback tuning.
- [x] 2.3 Add settings API, schema, migration, and dashboard tests.
- [x] 2.4 Run focused backend tests.
- [x] 2.5 Run focused frontend tests.
- [x] 2.6 Run `openspec validate --specs`.
