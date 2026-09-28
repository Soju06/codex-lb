## 1. Reproduce

- [x] 1.1 Add failing actual-scrape regressions for empty/populated account pools, status changes, deletion, and token expiry.
- [x] 1.2 Cover replicated whole-pool gauge aggregation and optional Prometheus support.

## 2. Implement

- [x] 2.1 Populate status counts and routing-eligible availability through the existing account-cache refresh, preserving routing behavior.
- [x] 2.2 Refresh the cache before metrics exposition and report refresh failure as a failed scrape.
- [x] 2.3 Document availability exclusions, multiprocess semantics, and alert examples; sync the normative specification.

## 3. Verify

- [x] 3.1 Run focused metrics/cache regression suites, lint, type checks, and strict OpenSpec validation.
- [x] 3.2 Verify every scenario and archive the completed change after independent review.
