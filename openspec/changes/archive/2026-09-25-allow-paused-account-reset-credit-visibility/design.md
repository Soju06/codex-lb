## Context

Dashboard weekly reset times are observation data, not permission to route requests. Credential maintenance already includes paused accounts. Reset-credit reads currently reuse redemption eligibility in several places, unnecessarily hiding information.

## Goals / Non-Goals

**Goals:** Allow selected-account live count reads and preserve last cached credit details while paused, using existing auth and account-bound egress controls.

**Non-Goals:** Background polling of paused accounts, changes to weekly usage refresh, routing, redemption, reauth/deactivated policy, new settings or schema changes.

## Decisions

Use the existing dashboard usage-reset-credits GET and credential refresh path. Remove only its paused guard. Keep consume guards and scheduler eligibility intact. Separate the cache-read guard from the redemption guard; allow paused summaries to join existing snapshots. The live count endpoint does not synthesize full credit snapshots from a count-only usage response.

The existing detail query runs on account selection and reports failures as unavailable. It can fetch a count after restart even with an empty snapshot cache. Cached summary data remains last-observed information, as with weekly reset metadata; it is not proof of fresh availability or redemption permission.

## Risks / Trade-offs

- Cached counts can age while paused → retain no-polling behavior and use the selected-account live count for inspection; do not promise fresh list badges.
- Credential refresh can fail permanently → preserve documented reauth-required transitions; never resume routing as part of observation.
- Paused accounts can be waiting for safe proxy routing → preserve route resolution and fail closed before upstream I/O.

## Migration Plan

No data migration or new configuration. Deploy through the normal release path. Production restart is outside this change.
