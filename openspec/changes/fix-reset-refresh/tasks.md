## 1. Reproduction and implementation

- [ ] 1.1 Reproduce stuck reset recovery with synthetic endpoint tests; verify failures on v1.24.0.
- [ ] 1.2 Share routed wake and refresh across consume paths; verify stale, already-fresh, failed-fetch, and proxy-binding cases.
- [ ] 1.3 Recover confirmed reset blocks and weekly-only accounts; verify cooldown, concurrency, and hard-block guards.
- [ ] 1.4 Reconcile runtime state and badge eligibility; verify selection and summary parity.
- [ ] 1.5 Bound natural rollover wake attempts durably; verify repeated ticks, concurrent attempts, and subsequent windows.

## 2. Validation

- [ ] 2.1 Run focused and full pytest suites with bounded CPU; record exact results.
- [ ] 2.2 Run lint, types, architecture, and strict OpenSpec validation; inspect changes for credentials and scope.
- [ ] 2.3 Sync verified requirements and archive this change, then open a fork PR and an upstream draft PR with reproduction evidence.
