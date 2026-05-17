## Why
Issue #537 reports that `GET /api/dashboard/overview` is slow once `usage_history` grows because the depletion / EWMA section rebuilds depletion state from raw usage rows on every request. The reporter measured 120 accounts × 117,600 usage_history rows × 7 day timeframe:

- dashboard with EWMA/depletion: median **2122.3 ms**
- dashboard with EWMA history fetch disabled: median **197.6 ms**
- EWMA/depletion share: **~90.7 %** of endpoint time

Dashboard polls are issued much more frequently than new usage rows land, yet each poll currently re-walks the full per-account history through `ewma_update`.

## What Changes
- Memoize per-account EWMA state alongside a signature of the in-window history `(earliest_recorded_at, latest_recorded_at, len(history))` so dashboard polls reuse the cached state whenever the input is unchanged.
- Invalidate the memoized state automatically when a new sample is appended, an older sample ages out of the window, or `reset_ewma_state()` is called.
- Continue recomputing the time-dependent fields (`risk`, `safe_usage_percent`, `projected_exhaustion_at`, `seconds_until_exhaustion`) on every call so polls remain live.

## Impact
- Cuts the dominant cost of `GET /api/dashboard/overview` in steady state: subsequent polls within the inter-arrival time of usage_history rows skip the per-account history replay entirely.
- No change to depletion semantics: the cache key captures the only inputs that influence EWMA rate, and any change to the in-window history forces a rebuild.
- No schema or persistence changes; the cache lives next to the existing in-memory `_ewma_states` map and clears on process restart.
