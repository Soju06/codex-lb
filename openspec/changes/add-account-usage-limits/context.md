# Reconciliation with PR #2147

Compared against Soju06/codex-lb#2147 at f777d8ec1cc65caf98687b02feff401e3f787a49. This is a consolidation proposal, not an implemented extension or a claim that either PR supersedes the other.

## Current contracts

| Concern | This change (#1528) | #2147 |
| --- | --- | --- |
| Threshold | One maximum-used percentage for applicable standard windows | Independent optional 5-hour and weekly maximum-used percentages |
| Disable/remove | Disabling retains the saved value; removing clears it | Each nullable window cap is independently disabled |
| Monthly plans | Normalized monthly and weekly-only observations participate | Monthly and nonstandard-duration windows are outside the two window caps |
| Missing/stale observations | Enabled policy fails closed with data_unavailable | reached_usage_cap_resets only blocks a present matching-duration row at its cap; it has no recorded-at freshness gate |
| Presentation | Provider usage remains visible alongside policy state | Reserved/usable segments, credit donuts and weekly pace reflect the configured reserve |
| Local error | account_usage_limit_reached | account_usage_cap_reached |

Both percentages denote the maximum fraction of provider quota that may be consumed, not a remaining-quota threshold. With 54% consumed and an 80% cap, 46 percentage points remain at the provider, 20 are reserved, and 26 are usable. #2147 adds that reserve presentation; #1528 does not.

Unequal window caps cannot be represented by this change's scalar. For example, primary/weekly usage of 65%/75% is allowed by respective caps of 70%/90%; 72%/75% is blocked by the primary cap. Replacing the pair with 70% would also block the first case on its weekly window. Treating either implementation as a drop-in replacement loses behavior.

## Consolidation design

Use one policy model and evaluator, extending this change's normalized-window evaluation and fresh owner-authorization path with optional primary and weekly overrides. Keep the scalar as the default for every applicable standard window, including monthly plans. An absent override inherits that default; standalone window caps without a scalar default affect only their specified windows. Preserve disable-versus-remove behavior and fail-closed telemetry handling for every enabled effective cap.

An existing enabled scalar value maps to the same default with no overrides, preserving its exact behavior. Independently configured values from #2147 map to matching explicit window overrides without creating an implicit monthly cap. Avoid two parallel admission gates, duplicate policy columns, or a lowest-threshold conversion. Resolve the public field/error naming once before exposing both interfaces.

Reuse #2147's accessible reserved/usable visualization against this single effective policy. Keep provider remaining quota distinct from usable quota and use the same duration normalization for selection, account summaries, donuts and pacing. Do not apply a weekly reserve to a monthly or other non-weekly observation. Preserve pinned ownership and capability authorization ordering when validating reused connections.

## Merge boundary and verification

The combined implementation includes the per-window extension and reserve visualization described above. It uses the existing usage-limit API with percent5H and percentWeekly additions, and returns effective window thresholds for dashboard consumers. Existing scalar rows retain their behavior through a new forward migration.

A consolidated implementation needs coverage for equal and unequal window caps, scalar migration and disable/remove semantics, weekly-only/monthly/nonstandard-duration plans, absent/stale observations, additional-quota routes, reused HTTP/WebSocket turns, cancellation cleanup, replica refresh, and accessible reserve/pacing calculations. Reuse the existing public-path tests in both PRs rather than duplicating their internal helper tests.


## Preview verification environment
The preview runs in a separate worktree with its own SQLite database and loopback port 8766. Seven synthetic accounts exercise default, unequal overrides, standalone override, disabled, reached, missing-data and monthly cases. Background provider polling is disabled using the repository browser-smoke harness, upstream points to an unused local port, and the preview permits only usage-limit and local dashboard-auth/consent writes. Production containers and databases are not accessed.

Reserved/usable display is inspired by PR #2147; this implementation extends the existing #1528 policy and editor rather than copying the other branch's admission gate.

CI run 34471699010 failed only test_dashboard_overview_combines_data: its synthetic usage lacked both duration and reset metadata, which correctly denotes no data under the reviewed shared mapper. The local fixture now supplies standard durations; the runtime no-data guard remains intact.


## Consolidation coverage

The combined implementation retains the independent-window and reserve-display requirements from #2147 through the existing #1528 policy path. It deliberately retains #1528's fail-closed behavior after telemetry expires; elapsed reset metadata alone does not constitute a fresh zero measurement.

| Requirement | Coverage |
| --- | --- |
| Unequal thresholds, weekly-only normalization, unrelated durations and unknown telemetry | account usage-limit unit suite |
| API persistence, immediate policy updates, disable-retain, explicit removal and standalone window limits | combined policy Accounts API integration test |
| Existing owner authorization | HTTP bridge second-turn test covers both shared and per-window policies; existing WebSocket and cancellation suites retain their authorization/cleanup contracts |
| Existing scalar rows and independent window policies | SQLite/PostgreSQL override migration round trip |
| Reserved versus provider/usable quota | quota bar, dashboard donut and account editor suites |
| Weekly pace uses usable capacity without scaling observed provider burn | weekly credit pace reserve test |

No separate cap cache or second admission evaluator is introduced. Existing ownership, retry, trusted-access and additional-quota tests continue to cover the canonical gate.
