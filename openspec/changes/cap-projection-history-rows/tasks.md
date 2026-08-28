## 1. Implementation

- [x] 1.1 Add a newest-first per-account row cap to the PostgreSQL bulk
      usage-history read (lateral top-N probe per account, composed with the
      existing per-account cutoffs; oldest-first slices preserved).
- [x] 1.2 Pass the cap from the dashboard projections history fetch, sized
      so the tail-weighted EWMA consumers (depletion, weekly-pace burn) see
      identical inputs.
- [x] 1.3 Exempt the configured pace-smoothing window from the cap
      (uncapped recent floor plumbed from the projections caller; disjoint
      floor + capped-tail branches in the lateral probe) so a per-request
      write burst can never truncate the equal-weight smoothing mean.
- [x] 1.4 Keep the SQLite snapshot-cache path on the shared floor (cap
      ignored, like cutoffs).

## 2. Validation

- [x] 2.1 Regression: capped slices equal the newest rows of the uncapped
      fetch, compose with per-account cutoffs, leave under-cap accounts
      untouched, and never drop rows at or after the uncapped recent floor;
      SQLite ignores the cap.
- [x] 2.2 PostgreSQL plan tests: the capped lateral probes (with and
      without the floor branch) stay index-only on the covering indexes.
- [x] 2.3 Unit test: the projections fetch supplies the cap and the
      smoothing-window floor.
- [x] 2.4 Run lint, type checks, sqlite + PostgreSQL test slices, and strict
      OpenSpec validation.
