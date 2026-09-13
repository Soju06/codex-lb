## 1. Implementation

- [x] 1.1 Treat `usage_limit_reached` as terminal in
  `_http_bridge_account_capacity_wait_seconds` (structured code, not message).

## 2. Verification

- [x] 2.1 Unit: the capacity-wait helper and wait plan return no wait for both
  selector message shapes; `rate_limit_exceeded` with the same hint still waits.
- [x] 2.2 Unit (virtual time): the bridge session-creation loop raises the `429`
  immediately with `resets_at`, no keepalives, no time consumed, one attempt.
- [x] 2.3 Integration: `/backend-api/codex/responses` returns `429`
  `usage_limit_reached` with `resets_at`, no `Retry-After`, no
  `codex.keepalive`, and never enters `_iter_account_capacity_wait_sse`.
- [x] 2.4 `ruff check`, `ruff format --check`, `ty check`,
  `scripts/check_proxy_architecture.py`, the bridge unit and integration
  suites, and `openspec validate terminate-http-bridge-usage-limit-capacity-wait --strict`.
