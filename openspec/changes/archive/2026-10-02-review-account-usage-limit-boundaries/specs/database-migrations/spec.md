## ADDED Requirements

### Requirement: Migration authoring accepts converged parallel history

Migration topology validation MUST reject a new revision branching from an upstream ancestor that already has descendants unless the checkout contains a revision joining that new lineage with every upstream head. A converged checkout MUST retain one canonical migration head and MUST NOT require rewriting the identifiers or parentage of previously applied revisions. A merge omitting an upstream head MUST NOT suppress the fork finding.

#### Scenario: Published parallel history is joined explicitly
- **GIVEN** a branch-local revision and an upstream revision descend from the same earlier revision
- **AND** a merge revision joins the local lineage with all upstream heads
- **WHEN** migration topology is checked against upstream
- **THEN** the converged lineage is accepted
- **AND** the existing revision identifiers and parentage are retained

#### Scenario: A merge omits the current upstream head
- **GIVEN** a new revision branches from an upstream ancestor
- **AND** its merge does not descend from every current upstream head
- **WHEN** migration topology is checked against upstream
- **THEN** the branch-fork finding remains an error
