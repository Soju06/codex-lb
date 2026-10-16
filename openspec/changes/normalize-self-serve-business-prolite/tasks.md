- [x] Add the upstream-to-canonical plan alias in the shared plan normalizer.
- [x] Cover canonicalization, rate-limit parsing, capacity, and model-plan
  eligibility for `self_serve_business_prolite`.
- [x] Add a product-path usage-refresh regression test proving that a
  workspace-less Team account persists `prolite` and writes usage.
- [x] Confirm unknown plan identifiers retain their existing behavior.
- [x] Run focused unit tests, lint/type checks, OpenSpec validation when the CLI
  is available, and `git diff --check`.
