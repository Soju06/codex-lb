# Quota continuity recovery implementation plan

**Goal:** Remove retry overlap, preserve continuity recovery, relocate the switch,
deploy latest main with three patches, then update PR #2207.
**Architecture:** Native retry/selection stays authoritative; the patch prepares
safe continuity and retires exhausted soft affinity.
**Spec:** specs/responses-api-compat/spec.md and specs/frontend-architecture/spec.md.

## Tasks

- [x] Incorporate latest main on the existing branch.
- [x] Test that recovery does not suppress ordinary failover or extend retries.
- [x] Remove extra retry counters/delays and relocate/reword the switch.
- [x] Validate streaming, WebSocket, bridge, continuity and ownership regressions.
- [x] Validate settings, rendered UI, migrations, lint, types and builds.
- [x] Refresh HomeServer bundle; rehearse migration on production backup.
- [x] Deploy only Codex LB and verify health and rendered switch.
- [x] Commit/push and update existing PR after successful deployment.

No independent review. Do not tune new resilience settings.
