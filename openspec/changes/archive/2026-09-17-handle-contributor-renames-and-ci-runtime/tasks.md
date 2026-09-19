## 1. Contributor identities

- [x] 1.1 Reproduce renamed numeric-noreply authors being treated as a second person.
- [x] 1.2 Resolve historical numeric author IDs with current GitHub identities while retaining unknown-ID, legacy-author, bot-filtering, pagination, and API-failure behavior.
- [x] 1.3 Update the existing renamed contributor and generate the README without duplicate people.

## 2. Integration runtime

- [x] 2.1 Set the integration-core job budget to 30 minutes without changing per-test deadlines, shard selection, or required aggregation.
- [x] 2.2 Validate workflow syntax, existing required-check tests, and shard partitioning.

## 3. Verification and review

- [x] 3.1 Run contributor regressions and the live PR-event coverage check.
- [x] 3.2 Run lint, type checks, strict OpenSpec validation, and independent review.
- [x] 3.3 Sync the verified automation requirements and record the remaining cloud-CI validation boundary.

Verification: the four focused automation suites passed 69 tests; the live
PR-event check covered 118 GitHub commit contributors. Lint, type checking,
actionlint, shard partitioning, simplicity budgets, and strict OpenSpec 1.11.0
validation passed. Independent review found no remaining change defects. The
updated GitHub workflow still requires a cloud run; the 30-minute budget does
not establish a cause or fix for the separately observed aiosqlite warning.
