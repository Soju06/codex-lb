# Change: remove-helm-pre-1-13-migration-shim

## Why

Chart 1.13.0 (#363) moved the app from a `Deployment` to a `StatefulSet` and added a shim to carry pre-1.13 releases across the kind change (two `legacy-deployment-*` hooks, the lookup-based `auto` selector mode behind `migration.serviceSelectorMode`, the `codex-lb.legacySelectorLabels` helper). #2211 kept it; #2230 documented it and announced a removal.

The migration has been available for five minors (1.13 - 1.24), and for every release installed on 1.13.0 or later the shim is a pure no-op that still costs two hook Jobs, two RBAC triples, an RBAC grant to patch/delete Deployments, a readiness wait on every upgrade, and a `lookup`-dependent selector that renders differently under `template` / `--dry-run` / `--dry-run=server`. The owner decided to drop it now (PRINCIPLES P1/P2).

## What Changes

- **REMOVED** `deploy/helm/codex-lb/templates/legacy-deployment-prepare-hook.yaml` and `legacy-deployment-cleanup-hook.yaml` (Job + ServiceAccount + Role + RoleBinding each).
- **REMOVED** the lookup-based selector branch in `templates/service.yaml`; the public Service now always renders `codex-lb.workloadSelectorLabels`.
- **REMOVED** the `codex-lb.legacySelectorLabels` helper and the `migration.serviceSelectorMode` value. A stale `migration.serviceSelectorMode` in an operator's values file is inert — it is not read and no longer changes the rendered selector.
- The planned-removal wording #2230 added ("the first minor release after 1.26") is rewritten rather than honoured: it has only ever existed in the unreleased `1.25.0` pre-release train (chart `1.25.0-beta.7`; `1.25.0` is not released), so no shipped chart ever carried that promise to operators. Leaving it would have made the README contradict the code.
- The chart README's `Upgrading` section now states the removal as a fact of this release and gives pre-1.13 operators the supported path: upgrade to a `1.24.x` chart first (which still runs the cutover), verify it, then upgrade to this release.
- Helm tests that asserted the shim renders are replaced by tests asserting it is gone: no hook templates, no `legacy` traffic lane anywhere in an `--is-upgrade` render, and the plain workload selector on both install and upgrade renders.

No behavior change for releases first installed on chart 1.13.0 or later, or for releases that already completed the cutover: they were already selecting the StatefulSet lane and the hooks were no-ops for them.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `deployment-installation`: the requirement that retained the shim through 1.26 and announced its removal is replaced by a requirement that the chart renders no pre-1.13 shim and that the README documents the two-step upgrade path for pre-1.13 releases.

## Impact

- Chart templates: `templates/service.yaml`, `templates/_helpers.tpl`; both `legacy-deployment-*-hook.yaml` deleted.
- Values: `migration.serviceSelectorMode` removed from `values.yaml` (it was never in `values.schema.json`).
- Docs: `deploy/helm/codex-lb/README.md` `Upgrade Contract` bullet and `Upgrading` section.
- Tests: `tests/unit/test_helm_external_secrets.py`.
- Operators still on a chart older than 1.13.0 must upgrade to a `1.24.x` chart before this release. Upgrading such a release straight to this chart would point the Service at StatefulSet pods that do not exist yet. Everyone else is unaffected and their upgrades get cheaper.

Part of the slop-removal campaign 0908 (follow-up to #2211 and #2230).
