## ADDED Requirements

### Requirement: Helm chart renders no pre-1.13 controller-migration shim

The Helm chart MUST NOT render the pre-1.13 `Deployment` -> `StatefulSet` controller-migration shim. Specifically: no `legacy-prepare` `pre-upgrade` hook and no `legacy-cleanup` `post-upgrade` hook (nor their ServiceAccount, Role and RoleBinding), no `legacy` traffic-lane selector helper, and no `migration.serviceSelectorMode` value. The public Service's selector MUST be the StatefulSet workload lane unconditionally, on install and on upgrade alike, and MUST NOT depend on a `lookup` of the live Service. A `migration.serviceSelectorMode` key left over in an operator's values file MUST be inert.

The chart README's `Upgrading` section MUST state that the shim is removed in this release and MUST give releases still on a chart older than 1.13.0 the supported path: upgrade to a `1.24.x` chart first so the cutover runs there, verify that the Service selects the StatefulSet lane and the legacy `Deployment` is gone, then upgrade to this release. That section MUST NOT promise a future removal date for the shim.

#### Scenario: Upgrade renders no migration-shim hooks

- **GIVEN** any release of the chart
- **WHEN** the chart is rendered with `--is-upgrade`
- **THEN** no `legacy-prepare` or `legacy-cleanup` Job, ServiceAccount, Role or RoleBinding is rendered
- **AND** no rendered resource carries the `codex-lb.soju.dev/traffic: legacy` lane

#### Scenario: Public Service always selects the StatefulSet lane

- **WHEN** the public Service is rendered on install, on upgrade, or with a stale `migration.serviceSelectorMode` override
- **THEN** its selector is exactly the workload selector labels, including `codex-lb.soju.dev/traffic: workload`

#### Scenario: Operator on a pre-1.13 chart reads the upgrade path

- **GIVEN** a release installed from a chart older than 1.13.0
- **WHEN** the operator reads the chart README's `Upgrading` section
- **THEN** it states that the shim is removed in this release
- **AND** it tells the operator to upgrade to a `1.24.x` chart first, plan that step as a maintenance window, verify the cutover, and only then upgrade to this release

## REMOVED Requirements

### Requirement: Helm pre-1.13 controller-migration shim is retained through 1.26 and its removal is announced

**Reason**: The owner decided to remove the shim now rather than carry it to the 1.26 minor. The migration has been available for five minors (1.13 - 1.24) and the shim is a pure no-op — two hook Jobs, two RBAC triples, a StatefulSet-readiness wait and a `lookup`-dependent selector — on every upgrade of every release installed on 1.13.0 or later. The requirement's own clause ("Removing the shim MUST be its own OpenSpec change that supersedes this requirement") is satisfied by this change. Replaced by "Helm chart renders no pre-1.13 controller-migration shim".

**Migration**: The retained requirement's announced removal date ("the first minor release after 1.26") only ever existed in the unreleased `1.25.0` pre-release train (chart `1.25.0-beta.7`), so no shipped chart carries that promise to operators. Releases first installed on chart 1.13.0 or later, and releases that already completed the cutover, need no action. A release still on a chart older than 1.13.0 MUST upgrade to a `1.24.x` chart first — the last chart that ships the shim — and confirm the cutover before upgrading to this release; the README documents the commands.
