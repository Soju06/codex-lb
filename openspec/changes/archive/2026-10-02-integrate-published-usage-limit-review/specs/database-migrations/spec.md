## ADDED Requirements

### Requirement: Usage-policy history convergence preserves both published schemas

The canonical migration head MUST accept databases from the local scalar lineage, the upstream-integrated scalar lineage, and the published override lineage. Upgrades MUST retain saved scalar and override values and apply all required upstream schema. Previously applied revision identifiers MUST remain recognized.

#### Scenario: A local scalar database adds published overrides
- **WHEN** a database from the reviewed local lineage upgrades to the canonical head
- **THEN** upstream schema and override columns are present
- **AND** existing saved scalar policies retain their values

#### Scenario: A published override database joins the reviewed lineage
- **WHEN** a database that already applied upstream schema and published override policies upgrades to the canonical head
- **THEN** it reaches one head without duplicate-schema failures
- **AND** enabled and saved default and override values are preserved
