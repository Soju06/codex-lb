## Why

Credit-backed ChatGPT accounts can remain usable after the regular quota
windows report full usage, but codex-lb currently treats those 100% quota
samples as hard blocks. Operators also cannot see the live credit balance on
the account cards, so the dashboard can disagree with routing behavior.

## What Changes

- Treat latest upstream credit state as an override for quota-derived
  `rate_limited` / `quota_exceeded` status when credits are present, unlimited,
  or have a positive balance.
- Preserve fresh credit metadata when primary and secondary usage samples are
  normalized for account summaries and load-balancer account state.
- Expose credit state on account summary responses and render a compact
  Credits value on dashboard account cards.

## Impact

- Modified capabilities: `usage-refresh-policy`, `frontend-architecture`
- Backend: quota status derivation, account summary mapping, load-balancer
  account state
- Frontend: account/dashboard schemas and account card rendering
- Tests: quota/status selection, account summary credit extraction, dashboard
  account card/schema coverage
