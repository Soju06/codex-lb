## 1. Implementation

- [x] 1.1 `sum_demand_window` read primitive: watermark-consistent SQL
      `SUM(request_count)` over folded demand slots plus the raw complement
      windows, degrading to a full-range raw window with no watermark.
- [x] 1.2 Route `_count_recent` through the demand rollup when every active
      listing filter maps onto a demand dimension (no search, no error-code
      splits); count the raw complement with the exact listing conditions.
- [x] 1.3 Keep the per-filter-signature TTL cache in front of both paths.

## 2. Validation

- [x] 2.1 Rollup parity harness: listing totals (default/status
      splits/window/account/api-key/model/effort/search-fallback shapes,
      with cancelled-status rows on both sides of the watermark) equal the
      legacy counts across every watermark state, concurrent folds, escape
      hatch, and retention pruning.
- [x] 2.2 Existing suites pass (`uv run pytest`), `ruff`, `ty`.
