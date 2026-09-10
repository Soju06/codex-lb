## ADDED Requirements

### Requirement: Automated review summaries avoid PR-body CI restarts

Repository CodeRabbit configuration MUST place generated high-level summaries
in the walkthrough comment rather than automatically updating the PR body.
Summary generation and automated review MUST remain enabled. CI MUST continue
handling PR edits so release evidence is revalidated; this optimization MUST NOT
skip or manufacture successful required CI checks.

#### Scenario: Review completes after a push

- **WHEN** CodeRabbit generates its automatic high-level summary
- **THEN** its configured destination is the walkthrough comment
- **AND** summary generation does not require an automatic PR-body update

#### Scenario: Release evidence is edited

- **WHEN** a maintainer edits a release PR's validation evidence
- **THEN** the existing edited-event CI and release guards remain active
