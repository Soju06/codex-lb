## 1. Implementation

- [x] 1.1 Add a newest-first per-account row cap to the PostgreSQL bulk
      usage-history read (lateral top-N probe per account, composed with the
      existing per-account cutoffs; oldest-first slices preserved).
- [x] 1.2 Exempt an uncapped recent floor from the cap (disjoint floor +
      capped-tail branches in the lateral probe) so a per-request write burst
      can never truncate an equal-weight consumer window.
- [x] 1.3 Size the cap to the EWMA tail (64 rows; the first row seeds the
      EWMA so the tail performs 63 updates, one per distinct recorded
      second, leaving a pre-tail residual of at most `0.6**63` times the
      largest per-second slope, ~1e-12 %/s worst case) instead of a time
      window at an assumed write cadence, and
      derive the floor as the wider of the configured pace-smoothing window
      and the 3-hour fleet-burn window on both projections fetches
      (weekly-only accounts sourced from the primary stream feed the weekly
      pace from the primary fetch).
- [x] 1.4 Hydrate PostgreSQL bulk-history rows positionally instead of via
      per-column `Row` attribute access; keep the per-account sort and the
      caller-side cutoff trimming.
- [x] 1.5 Replace the field-by-field `blake2b(repr)` depletion cache content
      digest with one fixed-width process-local hash over the row edge
      tuples; keep the SQLite bulk-cache digest unchanged.
- [x] 1.6 Keep the SQLite snapshot-cache path on the shared floor (cap and
      floor ignored, like cutoffs).

## 2. Validation

- [x] 2.1 Regression: capped slices equal the newest rows of the uncapped
      fetch, compose with per-account cutoffs, leave under-cap accounts
      untouched, and never drop rows at or after the uncapped recent floor;
      SQLite ignores the cap.
- [x] 2.2 PostgreSQL plan tests: the capped lateral probes (with and
      without the floor branch) stay index-only on the covering indexes; the
      covered read still matches the non-covered read.
- [x] 2.3 Unit tests: both projections fetches supply the EWMA tail cap and
      the `max(smoothing window, 3h)` floor, including a weekly-only
      primary-source account with and without `include_primary`.
- [x] 2.4 Unit tests: depletion over a 5000-row history equals the 64-row
      tail replay within 1e-12 (exactly when a reset lands inside the
      tail); a tail spanning 64 distinct recorded seconds matches within
      1e-12 however many rows share each second while a 64-row tail packed
      into fewer distinct seconds is the documented divergence boundary;
      a zero-to-high step followed by 64 flat one-per-second rows pins the
      seed-row residual (`0.6**63` times the step slope) and a saturated
      account pins the ghost-rate exhaustion-ETA divergence (risk and burn
      rate identical);
      weekly pace over a 7-day per-minute history equals the
      tail-bounded history within 1e-12; the content signature is stable
      for identical rows and changes for any corrected field including
      `None` variants.
- [x] 2.5 Run lint, type checks, sqlite + PostgreSQL test slices, and strict
      OpenSpec validation.
