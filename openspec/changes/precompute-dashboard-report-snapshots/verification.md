# Browser completion measurements

This synthetic benchmark measures default 7-day Dashboard and Reports pages,
not just their API responses. It shows a first-navigation improvement after
snapshot preparation, a smaller Dashboard repeat-load improvement, and no
Reports repeat-load improvement. It does not establish production latency.

## Results

Each cell is the median of five samples, followed by the observed range in
seconds. Every navigation uses a fresh browser context with UTC timezone,
English locale, and a 1440 × 1000 viewport.

| Backend state | Page | Upstream | Prepared snapshots |
|---|---|---|---|
| First navigation after process start | Dashboard | 3.296 (3.273–3.393) | 2.494 (2.467–2.500) |
| First navigation after process start | Reports | 4.438 (4.398–4.455) | 4.029 (4.011–4.092) |
| Repeat navigation, same backend | Dashboard | 2.683 (2.652–2.958) | 2.455 (2.353–2.477) |
| Repeat navigation, same backend | Reports | 3.993 (3.937–4.028) | 4.028 (3.974–4.062) |

The first-navigation median decreases by 24.3% for Dashboard and 9.2% for
Reports. Repeat Reports is 0.9% slower in this run; there is no repeat-load
speedup claim for that page. The fixed scrolling and render-stability waits
limit how much this small-workload comparison can show.

### Startup cost is separate

The browser timer starts **after readiness**, so it excludes snapshot preparation.
The first empty-snapshot candidate process takes 7.226 seconds to analytical
readiness, versus 3.371 seconds to ordinary health readiness upstream. Candidate
restarts with durable snapshots take 3.300, 3.260, 3.310, and 3.459 seconds;
upstream restarts take 3.287, 3.240, 3.297, and 3.241 seconds.

These are different readiness conditions by design. The candidate must prepare
Dashboard plus the UTC 7/30/90-day Reports presets before its analytical readiness
endpoint succeeds. This moves work out of HTTP requests; it does not eliminate
the work or prove a faster first-ever startup. Fresh custom filters and newly
registered timezones can still return a warming response and require a retry.

## Workload and comparison

- Baseline: upstream `72630bbfeee9e3b0d9acfb95e2b1e706b8b0188e`.
- Candidate implementation: `89ee7957`, with the separate snapshot regression
  tests. No averaging controls or alternate quota calibration are included.
- SQLite with 24 synthetic Pro accounts, 50,000 request logs spanning 28 days,
  and 13,824 quota observations. Names and emails are synthetic examples.
- Both source trees use the same Python 3.13 environment, frontend dependencies,
  Chromium executable, build mode, seed database, encryption key, and evaluation
  instant. The candidate applies its additive migration to its own copy.
- A single bounded Linux test unit runs both arms sequentially, with a 2-CPU
  quota, 4-GiB memory limit, no swap, and a 512-task limit. It is a shared host,
  not an isolated machine; baseline runs precede candidate runs rather than
  being randomized. Five samples are descriptive, not a significance test.
- Each sample starts a fresh backend. Each backend serves a first and repeat
  browser pass. Snapshot data persists between candidate restarts; process-local
  caches do not. OS and SQLite page caches are not deliberately dropped.
- The harness disables provider polling, catalog, telemetry, and maintenance
  loops using the existing browser-smoke seams. It leaves the snapshot producer
  enabled. Telemetry consent is already disabled in the isolated seed, so no
  first-run dialog or opt-out request covers the page.
- Imported application `utcnow` functions use one fixed evaluation instant for
  both arms. Monotonic performance clocks, HTTP, SQL, and rendering remain real.
  This keeps moving-window totals comparable while avoiding live provider calls.
- Page-specific business-summary parity is being rechecked. The initial recorder
  could let a Dashboard prefetch overwrite the Reports summary. Completion
  timing still waited for the required routes, but the earlier claim of 20
  matched page-summary pairs is withdrawn until route-specific capture passes.
  The corrected recorder binds each summary to the page being measured.

## Completion boundary

`frontend/browser-smoke/page-completion.mjs` starts a monotonic timer immediately
before navigation. It waits for successful JSON response bodies from the actual
backend, scrolls through the full document to trigger below-fold panels, waits
for outstanding API requests, checks for a populated summary and rendered table,
and waits for the main DOM to remain unchanged for 300 ms plus two animation
frames. It fails on API errors, a covering dialog, or its timeout. It then
captures a full-page screenshot; screenshot encoding is outside the timer.

The required Dashboard routes include overview, projections, request logs, and
log options. Reports includes the report, options, and thread identity. The
measurement includes the default page's charts, tables, lazy chunks, React
rendering, and deliberate scroll/stability waits. It is not a paint trace,
interaction-latency measurement, HTTP-only latency, or a measurement of the
five-minute refresh cadence. Screenshots were inspected to confirm rendered
panels and the absence of a first-run overlay.

## Local qualification

- Backend: 257 passing snapshot, migration, readiness, report, permission, and
  lifecycle tests; two real-PostgreSQL repeatable-read checks skipped on SQLite.
  A further 75 Dashboard, conversation, permission-preset, and usage-flow tests
  pass. This is scoped backend coverage, not the full backend or PostgreSQL suite.
- Frontend: all 1,663 tests in 184 files pass. Scoped ESLint, TypeScript build
  checking, and the production build pass.
- Python: Ruff check/format on changed files, `ty check app`, and the five
  architecture checks pass. Migration topology also passes against the fetched
  upstream `main`, with one added revision and a single head.
- OpenSpec: all 67 canonical capabilities and the strict active change validate.
- These are local results. Hosted CI, current-head review threads, and merge
  eligibility are separate checks; this document does not claim they are green.

## Reproduce

Build both frontend trees with the same installed dependencies. Use isolated
source checkouts and new output directories, never a live data directory. From
the candidate tree, run the following commands with the same Python interpreter:

```sh
python scripts/benchmark_dashboard_pages.py \
  --source-root BASELINE_SOURCE --output BENCHMARK_BEFORE --samples 5
python scripts/benchmark_dashboard_pages.py \
  --source-root CANDIDATE_SOURCE --output BENCHMARK_AFTER \
  --seed-from BENCHMARK_BEFORE/seed --samples 5
```

`BASELINE_SOURCE` and `CANDIDATE_SOURCE` are absolute source checkout paths.
The output parents must exist, but each output directory must be new. Bun,
Playwright, and a compatible Chromium must already be installed. Set
`PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` to reuse an existing browser executable.
The harness starts only ephemeral loopback backends and joins them on exit.

The [individual samples](evidence/page-completion.json) retain both first and
repeat results. The screenshot pairs show the synthetic default pages:

| Page | Before | After |
|---|---|---|
| Dashboard | [Full page](evidence/dashboard-before.png) | [Full page](evidence/dashboard-after.png) |
| Reports | [Full page](evidence/reports-before.png) | [Full page](evidence/reports-after.png) |
