# Timeout Invariant Linter Audit Disposition

Inputs: `LINTER_AUDIT.md` and the 11 PR #1622 inline findings fetched with
`gh api repos/Soju06/codex-lb/pulls/1622/comments`.

| # | Rule | Audit verdict | Bot finding | Disposition |
|---:|---|---|---|---|
| 1 | `upstream-connect-within-proxy-budget` | CIRCULAR | Wrong connect anchor | Removed; generic client clamps connect to total budget, so this was circular. |
| 2 | `upstream-connect-within-stream-budget` | GROUNDED | Wrong connect anchor family | Deferred; anchor fixed to `app/core/clients/proxy.py:2720` but not enforced by the shipped registry. |
| 3 | `upstream-connect-within-compact-budget` | CIRCULAR | None | Removed; compact passes remaining budget as an override/clamp. |
| 4 | `upstream-connect-within-bridge-budget` | CIRCULAR | None | Removed; bridge request budget does not directly own upstream connect. |
| 5 | `admission-plus-connect-within-proxy-budget` | GROUNDED | Wrong phase/circular deadline | Removed; runtime recomputes remaining absolute budget after admission. |
| 6 | `admission-plus-connect-within-compact-budget` | GROUNDED | Wrong phase/circular deadline family | Removed; compact also passes remaining deadline-derived overrides. |
| 7 | `admission-wait-within-proxy-budget` | GROUNDED | None | Kept. |
| 8 | `admission-wait-within-stream-budget` | GROUNDED | None | Kept. |
| 9 | `admission-wait-within-compact-budget` | GROUNDED | None | Kept. |
| 10 | `admission-wait-within-bridge-budget` | CIRCULAR | None | Removed; bridge admission is clamped to remaining bridge budget. |
| 11 | `stream-idle-within-stream-budget` | GROUNDED | Bot says total may precede idle | Removed; total and idle are independent aiohttp limits. |
| 12 | `stream-idle-within-bridge-budget` | GROUNDED | Bot says same phase family | Removed; outer bridge deadline may validly fire before idle. |
| 13 | `sse-keepalive-before-stream-idle` | GROUNDED | Downstream keepalive cannot reset upstream idle | Removed; it compared independent directions. |
| 14 | `sse-keepalive-within-stream-budget` | GROUNDED | None | Deferred; not enforced by the shipped registry. |
| 15 | `sse-keepalive-within-bridge-budget` | GROUNDED | None | Deferred; not enforced by the shipped registry. |
| 16 | `token-refresh-claim-covers-admission-and-exchange` | GROUNDED | None | Deferred; not enforced by the shipped registry. |
| 17 | `refresh-failure-cooldown-within-claim-ttl` | GROUNDED | Cooldown is process-local cache | Removed; cooldown does not extend claim ownership. |
| 18 | `token-refresh-exchange-within-claim-ttl` | GROUNDED | None | Deferred; not enforced by the shipped registry. |
| 19 | `usage-fetch-within-refresh-interval` | WRONG | Scheduler serializes, cadence may slip | Removed. |
| 20 | `usage-fetch-within-reset-credits-interval` | WRONG | Usage cadence family | Removed. |
| 21 | `compact-budget-within-proxy-budget` | CIRCULAR | Compact lane independent | Removed. |
| 22 | `bridge-idle-ttl-within-bridge-budget` | WRONG | Reuse TTL may exceed request budget | Removed. |
| 23 | `bridge-codex-idle-ttl-within-bridge-budget` | WRONG | Reuse TTL family | Removed. |
| 24 | `bridge-stuck-gate-retire-after-admission` | GROUNDED | Separate phase from admission | Removed; response-created acknowledgement retirement is independent from queue admission. |
| 25 | `bridge-stuck-gate-retire-within-bridge-budget` | GROUNDED | Missing hard-anchor 2x multiplier | Fixed; now compares `2 * retire_after` with bridge budget. |
| 26 | `bridge-clean-close-jitter-within-admission` | CIRCULAR | Jitter/admission independent | Removed. |
| 27 | `bridge-clean-close-jitter-within-bridge-budget` | GROUNDED | None | Deferred; no settings field anchors the clean-close jitter, so the rule has nothing to read. |
| 28 | `account-lease-ttl-covers-proxy-budget` | GROUNDED | None | Kept. |
| 29 | `account-lease-ttl-covers-compact-budget` | GROUNDED | None | Kept. |
| 30 | `model-registry-snapshot-outlives-refresh-interval` | Proposed new | None | Added; `model_registry_snapshot_max_age_seconds > _REFRESH_INTERVAL_SECONDS`. |
| 31 | `durable-bridge-retry-circuit-ttl-covers-backoff-and-half-open` | Proposed new | None | Added; retry-circuit state TTL must outlive max backoff and half-open lease. |

Shipped registry: `TIMEOUT_INVARIANT_RULES` enforces exactly the eight rules
above marked Kept, Fixed, or Added (rows 7, 8, 9, 25, 28, 29, 30, 31), and
`tests/unit/test_timeout_invariants.py` pins that count. Rows marked Deferred
were judged grounded by the audit but ship unenforced: this validator runs at
startup and can abort the process under
`CODEX_LB_TIMEOUT_INVARIANT_VALIDATION_STRICT`, so a rule enters the registry
only once it has a settings anchor and a default configuration that satisfies
it.

Validation scope:

- Validated: startup `Settings` fields and imported constants used by the rule
  table.
- Not validated: per-request `ContextVar` overrides, runtime clamps, derived
  effective values, and database/API-key/model-source timeout inputs loaded
  after startup.
