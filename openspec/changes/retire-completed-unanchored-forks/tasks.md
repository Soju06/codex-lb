## 1. Fork Lifecycle

- [x] 1.1 Detect ordinary request-scoped unanchored fork lanes after successful completion.
- [x] 1.2 Retire only when pending, queued, admission, and handoff ownership are quiescent.
- [x] 1.3 Preserve durable aliases and exclude canonical and account-neutral recovery lanes.

## 2. Verification

- [x] 2.1 Cover immediate lease release and full lane close after completion.
- [x] 2.2 Cover multiple pending requests and admission/handoff races.
- [x] 2.3 Cover repeated forks beyond the account stream cap and durable continuation.
- [x] 2.4 Run focused tests, bridge integration, lint, type checking, architecture, and OpenSpec validation.
