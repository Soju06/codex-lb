# Verification

- Sticky-session API and read-only routing observation integration suite: 26 passed.
- Final affected frontend suite: 106 passed.
- Frontend TypeScript check and production build passed.
- Playwright completion-test and soft-account-switch scenes: 2 passed.
- Screenshot review verified the dialogs, target picker and completed result.
- Ruff, formatting, type checks and proxy architecture checks passed.
- The observation endpoint reads a detached runtime snapshot without selecting
  accounts or mutating the live leases/health.
- Manual reassignment uses a compare-and-set and rejects stale owners, hard or
  reserved keys, unavailable targets and preserved accounts.

The browser scenes use mocked accounts. The account completion sender has
separate actual-core-transport regressions in `verify-account-probe-completion`.
Live deployment to the local Docker service completed using an independent
backup/readiness/rollback script. All four account IDs, the API-key ID, 19 hard
continuation mappings, settings and encryption-key fingerprints were preserved.
Readiness returned HTTP 200. The existing effective HTTP and WebSocket ingress
budgets were retained. Per-account completion results are recorded separately.
