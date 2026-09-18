# Context

This is a focused port of the public timing fix onto current main. The upstream already separates terminal timing for routing throughput from total request latency; preserve both that distinction and the owner clock facade. Persist the terminal anchor for dashboard/report calculations, while routing weights retain their existing sampling definition.

Synthetic example: TTFT at 500 ms, upstream completion at 1000 ms and 100 non-reasoning output tokens give 200 TPS under the historical formula. Adding 2000 ms of local settlement incorrectly changes that formula to 40 TPS. New estimates use the first non-reasoning output anchor and the upstream terminal.

At least two observed chunks and a 100 ms window are a sample-quality policy, not a model speed cap. A ten-token, five-millisecond single-chunk response has insufficient evidence. Old rows retain nullable fields and separately labelled historical estimates, excluded from qualified report medians. Unknown reasoning usage stays unknown.

Only synthetic fixtures and public source facts belong in this change; no deployment data or private artifacts are included.

## Validation

- The request-log API covers reasoning-first output, cleanup delay, short and single-chunk responses, historical rows, missing usage and invalid timestamp order.
- The persistence funnel preserves total latency and the original routing-throughput numerator/window independently of the display estimate.
- The additive migration sits on the current main head and preserves historical rows through upgrade, downgrade and re-upgrade; new timing evidence remains null.
- Reports compare SQL eligibility with the shared speed policy, preserve report range limits and distinguish missing medians from measured zero.
- Source forwarding regression tests cover reasoning usage in both logging entrypoints, numeric limits, fragmented UTF-8/SSE framing and unchanged forwarding behavior.
- Dashboard comparisons in `evidence/` use synthetic fixtures with API requests disabled.

PostgreSQL-specific migration cases and the full hosted CI matrix remain CI merge gates.

- Proxy regressions: 2,920 tests passed, with the one loopback-bound test rerun successfully outside the socket sandbox. Final timing/helper/fast-path checks passed (50 tests), as did the affected WebSocket checks (28 tests).
- Model-source forwarding/routing: 306 tests passed. Reports/cache: 89 passed. Request-log API, repository, timing and routing-cohort checks passed; the final migration suite passed 49 tests with 9 PostgreSQL-only skips.
- Frontend request-list/report tests: 226 passed. TypeScript, ESLint, production build and synthetic screenshot check passed. Ruff, full Python type checks, proxy architecture, cancellation safety, timing seams, settings tiers, migration topology and simplicity checks passed.
- The full real-backend browser smoke passed all 8 tests on both baseline and this change. An intermittent 1440-pixel viewport overflow was also reproduced on the unmodified baseline; the isolated changed-head viewport check passed. No unrelated layout change is included.
