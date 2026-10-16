## 1. Compact main-path budget

- [x] 1.1 Raise the default `compact_request_budget_seconds` to 900 s without
  touching the account lease TTL.
- [x] 1.2 Bound the core compact client by the configured budget when no
  per-request override is present, and by the smaller value when both are.
- [x] 1.3 Keep the compact SSE idle deadline independent of the total budget.

## 2. Regression coverage

- [x] 2.1 Prove the compact product path pushes an 870 s upstream window
  under default settings.
- [x] 2.2 Prove an explicit smaller compact budget still caps the core client.
- [x] 2.3 Prove the aiohttp total/read timeouts follow the real default
  settings instead of being unset.
- [x] 2.4 Retain the existing compact cancellation and timeout-settlement
  coverage; derive automations reclaim-window fixtures from the effective
  budget instead of a hard-coded 180 s window.
