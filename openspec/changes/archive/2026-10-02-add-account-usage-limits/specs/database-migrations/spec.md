## ADDED Requirements

### Requirement: Migration authoring accepts converged parallel history

Migration topology validation MUST reject a new revision that branches from an upstream ancestor with existing descendants unless the checkout has a revision joining that lineage with every upstream head. A converged checkout MUST keep one canonical head and MUST NOT require rewriting the identifiers or parentage of applied revisions. A merge that omits an upstream head MUST NOT suppress the fork finding.

#### Scenario: Parallel history is joined explicitly

- **GIVEN** a branch revision and an upstream revision descend from the same earlier revision
- **AND** a merge revision joins the branch lineage with every upstream head
- **WHEN** migration topology is checked against upstream
- **THEN** the converged lineage is accepted with its revision identifiers and parentage unchanged

#### Scenario: A merge omits the current upstream head

- **GIVEN** a new revision branches from an upstream ancestor
- **AND** its merge does not descend from every current upstream head
- **WHEN** migration topology is checked against upstream
- **THEN** the fork finding remains an error

### Requirement: Usage-limit migrations converge on one head

Databases from the scalar usage-limit lineage, the upstream-integrated lineage, the per-window override lineage, and the upstream head MUST all upgrade to the one canonical head. Upgrades MUST apply every required upstream schema change, keep saved enabled flags and default and override percentages, and add disabled policy fields to databases that had none. Previously applied revision identifiers MUST remain recognized.

#### Scenario: A scalar usage-limit database upgrades

- **GIVEN** a database applied the scalar usage-limit revision and saved enabled and disabled policies
- **WHEN** it upgrades to the canonical head
- **THEN** upstream schema and override columns are present and the saved policies are unchanged

#### Scenario: An override database joins the canonical lineage

- **WHEN** a database that already applied upstream schema and per-window overrides upgrades to the canonical head
- **THEN** it reaches one head without duplicate-schema failures
- **AND** its default and override values are unchanged

#### Scenario: An upstream database adds usage limits

- **WHEN** a database at the upstream head upgrades to the canonical head
- **THEN** usage-limit fields are added with every policy disabled
