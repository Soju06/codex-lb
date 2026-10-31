# Verification

The routed streaming regression reproduced `preferred_account_unavailable`
after HTTP 401 on full history with encrypted reasoning. Both permanent forced
refresh failure and repeated HTTP 401 now complete on a replacement using the
existing safe projection. The original client payload remains unchanged.

- Focused proxy run: 35 passed, including hard-owner and ambiguous transport controls.
- Final auth and settlement regression run: 14 passed.
- Ruff, formatting, type checks and proxy architecture checks passed.
- Keyed permanent rejection quarantines selection before settlement and writes
  durable health only after reservation settlement.

All regression upstreams are deterministic test doubles. Live validation is
recorded separately after deployment.
