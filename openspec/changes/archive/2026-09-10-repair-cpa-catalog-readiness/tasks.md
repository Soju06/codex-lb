## 1. Repair and prove affected behavior

- [x] 1.1 Reproduce and repair legacy/bootstrap catalog migrations.
- [x] 1.2 Preserve reasoning levels with missing or null descriptions through the Codex catalog.
- [x] 1.3 Serve stored catalogs after refresh database failures and avoid batch starvation.
- [x] 1.4 Disable old rows on mode transitions without losing unavailable ownership.
- [x] 1.5 Normalize nested allowed-tool aliases through CPA Responses routes.
- [x] 1.6 Clarify remote TLS and local HTTP operating context.

## 2. Verify delivery

- [x] 2.1 Run affected tests, lint, type checks and strict OpenSpec validation.
- [x] 2.2 Review the candidate against the pinned upstream base.
- [x] 2.3 Assign hosted verification to this repair worker and subsequent monitoring to PR readiness.
