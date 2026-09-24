## 1. Capture and persist timing evidence

- [x] 1.1 Preserve total latency and routing semantics; capture first non-reasoning output and terminal observations using the owner clock.
- [x] 1.2 Add nullable request-log fields on the current migration head, with upgrade/downgrade and historical-row coverage.
- [x] 1.3 Preserve optional source usage and safely parse source SSE/timing values.

## 2. Expose qualified metrics

- [x] 2.1 Centralize backend TPS eligibility and expose estimate status.
- [x] 2.2 Update report medians/sample counts and dashboard display.

## 3. Verify

- [x] 3.1 Run focused product-path, migration and UI regressions plus relevant static checks.
- [x] 3.2 Verify schema/query parity and unchanged routing/ownership/settlement behavior.
- [x] 3.3 Validate strict OpenSpec, sync main spec and archive verified change.
- [x] 3.4 Review complete published diff and PR body for scope and privacy.
