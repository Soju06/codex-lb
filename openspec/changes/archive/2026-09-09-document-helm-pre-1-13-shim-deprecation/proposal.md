# Change: document-helm-pre-1-13-shim-deprecation

## Why

Chart 1.13.0 (#363) moved the application from a `Deployment` to a `StatefulSet` and added a three-piece migration shim (`legacy-deployment-prepare-hook.yaml`, `legacy-deployment-cleanup-hook.yaml`, the lookup-based `auto` Service selector mode behind `migration.serviceSelectorMode`). #2211 kept the shim because releases installed before 1.13.0 still depend on it, but the chart README never described the upgrade path, the shim's limits, or when the shim goes away. Announcing the removal is a compatibility commitment to Helm operators (AGENTS.md: operator-contract and compatibility changes are OpenSpec-gated), so the commitment is recorded here rather than only in the README.

## What Changes

- The chart README gains an `Upgrading` section: what the hooks do, the `migration.serviceSelectorMode` knob and what it does and does not control, verification commands keyed on the chart fullname, the caveat that the first upgrade from a pre-1.13 release is not zero-downtime (Helm removes the legacy Deployment during the resource sync, before the post-upgrade hook), and a `1.24 -> 1.25` note about the settings that left the environment.
- Deprecation announced: the shim is planned for removal in the first minor release after 1.26. Until then the chart keeps rendering it; the removal itself is a separate OpenSpec change.
- `values.yaml` marks `migration.serviceSelectorMode` as deprecated with a pointer to the README.

No template or default changes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `deployment-installation`: ADDED requirement — the pre-1.13 controller-migration shim is retained through the 1.26 minor, its upgrade path and limits are documented in the chart README, and its removal is announced there ahead of time.

## Impact

- Docs: `deploy/helm/codex-lb/README.md`, `deploy/helm/codex-lb/values.yaml` comments.
- Operators: releases first installed on 1.13.0 or later are unaffected. Releases still on a pre-1.13 chart must upgrade to any 1.13.0 - 1.26.x chart before the shim is removed.

Part of the slop-removal campaign 0908 (follow-up to #2211).
