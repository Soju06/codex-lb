# Change: surface-thread-identity-metrics

## Why

The owner has to choose between keeping the outbound thread identity **shared**
across the account pool and making it **isolated** per account, and today there
is no dashboard surface that shows what either choice would cost. Everything
known about the trade-off came from ad-hoc SQL run by hand against production:

- Conversations with three or more requests were served by a mean of **2.29**
  accounts on 09-09, **3.45** on 09-10 (an upstream incident) and **1.02** on
  09-11 (quiet upstream). The spread is driven by failover and soft-sticky
  rerouting under upstream pressure, not by a structural defect.
- Keyed traffic held a **91.0%** cache hit ratio on 09-09 while switching
  accounts, because the upstream prefix cache is node-local and is **not**
  partitioned per account. Unkeyed traffic held **51.2%** on the same day and
  **0.1%** on 09-11.
- **33-38%** of requests carry neither `conversation_id` nor `session_id`, so
  they are not grouped into conversations at all.

Those numbers move with upstream health, so a one-off measurement cannot settle
the decision; the operator needs to watch them. This change turns the ad-hoc
SQL into a Reports card.

## What Changes

- New `GET /api/reports/thread-identity` endpoint, scoped by the existing
  Reports `start_date` / `end_date` / `timezone` parameters and by nothing else.
  It returns keyed and unkeyed facets of the window carrying the
  accounts-per-conversation factor, the single-account conversation share, the
  turn-to-turn account switch rate, the cache hit ratio and the request share,
  plus the thresholds each figure was computed with.
- The Reports page renders a "Thread Identity & Cache Locality" card driven by
  that endpoint. The card joins the existing Charts visibility picker, so the
  query is issued only while an operator keeps the card on screen.
- Metric definitions, fixed so the card reproduces the baseline measurements:
  - **Thread key** — `conversation_id` normalized, else `session_id`
    normalized, else (unkeyed only) `api_key_id`. Reuses the single
    normalization SQL that the conversation fold already shares
    (`normalized_thread_key_expr`, extracted from `conversation_id_expr`).
  - **Accounts per conversation** — mean `count(DISTINCT account_id)` over
    threads with at least 3 requests, and the share of those served by exactly
    one account.
  - **Turn-to-turn account switch rate** — of consecutive requests in the same
    thread less than 600 s apart with an attributed account and a recorded,
    non-decreasing `input_tokens` on both ends, the share whose account
    changed. Requiring the prefix size to be *recorded* matters: 9.5% of rows
    over 09-09..11 have `input_tokens IS NULL`, and treating an unknown prefix
    as zero overstated the 09-09 keyed switch rate by 9% (4,112 switches over
    54,998 turns versus 3,744 over 54,154).
  - **Shared key namespace** — `conversation_id` and `session_id` are one
    identifier space, not two namespaces. Over 09-09..11, 107,628 of the
    128,260 rows carrying both held the *same* value and only 25 rows carried a
    conversation id without a session id, so qualifying the key by its source
    would split one real thread whenever a client stops sending the
    conversation id mid-thread.
  - **Cache hit ratio** — `sum(cached_input_tokens) / sum(input_tokens)` over
    `status = 'success'` requests with `input_tokens > 5000`.
  - **Unkeyed request share** — requests carrying neither identifier, over all
    requests in the window.
- Row scope matches `report_source`: internal warm-up traffic is excluded and
  soft-deleted rows are kept (deletion only detaches `account_id`, and
  `count(DISTINCT account_id)` skips NULLs). With that scope the queries
  reproduced the baseline exactly when it was taken — 09-09 keyed cache
  **90.98%**, unkeyed **51.20%**, 1,929 conversations at mean **2.292**
  accounts, 45.3% single account, max 22; 09-10 2,674 conversations at
  **3.453**; 09-11 **1.022**.
- **Account deletion decays the historical factor, and the panel says so.**
  Re-running the same statements 40 minutes later returned 09-09 mean
  **2.185** and 09-10 **3.338** from the identical conversation counts, because
  an account deletion in flight had raised 09-09's detached rows from 2,110 to
  6,419 of 99,681 and 09-10's to 10,323 of 91,615. Cache ratios and request
  shares were unaffected (deletion does not touch token columns). Each facet
  therefore also reports its unattributed request share and the card discloses
  it, so a decayed factor is visible rather than read as a genuine drop in
  account spread.
- Cost control: the window is capped at **7 days** (the ceiling the speed
  medians already use). A wider range answers `available: false` instead of
  scanning, exactly as `speed_metrics_available` does. Results are cached for
  300 s in the existing `ReportCache`, which is generalized from a fixed
  `ReportCacheKey` to any hashable key so the new cache reuses it.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `reports`: ADDED requirements "Thread identity metrics are served per keyed
  and unkeyed facet", "Thread identity metrics are pool-wide and window-capped"
  and "Approximate and decayed thread identity figures are disclosed".

## Impact

- Code: `app/modules/reports/{api,cache,repository,schemas,service}.py`, new
  `app/modules/reports/thread_identity.py`,
  `app/modules/accounts/usage_time_rollup.py` (expression extracted, behaviour
  unchanged), `app/core/usage/logs.py` (`SUCCESS_STATUS` named),
  `frontend/src/features/reports/**`, three locale files.
- API/schema: one added read-only endpoint. No database migration, **no new
  index**: both statements range-scan `requested_at` through the existing
  `idx_logs_requested_at_id` / `idx_logs_status_error_time`.
- Settings: none (the ceiling, thresholds and TTL are application constants,
  not operator tunables; the settings-field ratchet is unchanged).
- Operators: the card is on by default for a fresh dashboard and appears in the
  Reports "Charts" picker. A dashboard that already stored a chart selection
  keeps it hidden until it is enabled there.
- Forward compatibility: #2352 has since landed, so
  `request_logs.sticky_key_source`, `sticky_kind` and `sticky_key_hash` now
  exist. Nothing here reads them; splitting these metrics by affinity source is
  a follow-up that only needs the new column added to the one scope subquery
  in `thread_identity.py` and a third grouping column. Keeping it out holds
  this PR to one concern, and the historical rows those columns were backfilled
  as NULL would read as a single "unknown" bucket for the windows the baseline
  was measured over anyway.
