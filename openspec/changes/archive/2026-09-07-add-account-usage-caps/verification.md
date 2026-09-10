# Verification

All three added requirements are implemented and have regression coverage.

- Focused backend suite: 90 passed (account caps, migration round trip, bridge idle leases, load balancer integration, live usage ingestion, and usage-refresh recovery).
- Separate caps / guest-access / direct-WebSocket regression run: 26 passed. Guest writes are denied; an existing WebSocket rejects a capped turn, releases its reservation, and resumes after cap recovery.
- Accounts frontend and dashboard account surfaces: 218 passed across 20 files, including controls and all four bar surfaces.
- TypeScript build check, changed-file ESLint, changed-file Ruff, Vite production build, and git diff whitespace checks passed.
- Temporary-database CLI upgrade/check: current revision 20260907_000000_add_account_usage_caps, migration_policy=ok, schema_drift=none. No operator database was migrated.
- Strict change validation and both affected main specs passed. Whole-repository strict validation has unrelated pre-existing failures: a clean HEAD spec snapshot also reports 23 failing specs (including the two placeholder purposes corrected in this change). Unrelated specs were not changed.

Coverage includes inclusive thresholds, either-window blocking, both-window recovery, weekly-primary normalization, monthly/missing-window exclusion, config removal, standard-cap enforcement for additional-quota routing, account-pinned ownership, soft-sticky fallback, validation atomicity, default-null historical rows, read-only controls, and marker positions.

Review was performed directly in the parent session; the background subagent runner is unavailable in this Pi installation. Browser screenshots and the full repository test suite were not run. Usage caps use observed provider usage and existing cache propagation; in-flight work and sampling delay can overshoot thresholds.
