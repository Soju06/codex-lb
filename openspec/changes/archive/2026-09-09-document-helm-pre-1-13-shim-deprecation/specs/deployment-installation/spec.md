## ADDED Requirements

### Requirement: Helm pre-1.13 controller-migration shim is retained through 1.26 and its removal is announced

The Helm chart MUST keep rendering the pre-1.13 controller-migration shim (the `pre-upgrade` legacy-prepare hook, the `post-upgrade` legacy-cleanup hook, the lookup-based `auto` Service selector mode and the `migration.serviceSelectorMode` value) on every chart release up to and including the 1.26 minor. The chart README MUST document the upgrade path from chart versions older than 1.13.0: what each hook does, what `migration.serviceSelectorMode` controls (the rendered selector only; the cleanup hook cuts the live Service over regardless), how to verify the cutover using the chart fullname, and that the first upgrade from a pre-1.13 release is not zero-downtime because Helm removes the legacy Deployment during the resource sync before the post-upgrade hook runs. The README MUST state the planned removal (the first minor release after 1.26) and the intermediate-upgrade path for releases still on a pre-1.13 chart before the removal ships. Removing the shim MUST be its own OpenSpec change that supersedes this requirement.

#### Scenario: Operator upgrades a release installed before chart 1.13.0

- **GIVEN** a release installed from a chart older than 1.13.0 and any chart up to the 1.26 minor
- **WHEN** the operator runs `helm upgrade`
- **THEN** the chart renders the legacy-prepare and legacy-cleanup hooks and the `auto` selector mode
- **AND** the chart README tells the operator to plan a maintenance window, how to verify the cutover, and what `migration.serviceSelectorMode` does and does not control

#### Scenario: Operator reads the deprecation notice

- **WHEN** an operator reads the chart README's `Upgrading` section
- **THEN** it states that the shim is planned for removal in the first minor release after 1.26
- **AND** it states that a release still on a pre-1.13 chart must first upgrade to a 1.13.0 - 1.26.x chart
