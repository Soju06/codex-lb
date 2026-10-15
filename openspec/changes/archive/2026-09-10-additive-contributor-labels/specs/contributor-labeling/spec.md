## ADDED Requirements

### Requirement: Additive PR area labels

The PR labeler SHALL add existing labels for documentation, Python, frontend, CI, Docker, and database migration paths. It SHALL retain labels when paths later stop matching.

#### Scenario: Documentation and migration changes
- **WHEN** a PR changes AGENTS.md and an Alembic version file
- **THEN** it receives documentation, python, and db migration labels
- **AND** existing labels remain unchanged

#### Scenario: Path removed on a later head
- **WHEN** a later PR head no longer contains a previously labeled area
- **THEN** the labeler does not remove that area's label

### Requirement: New issue classification

On issues opened, the workflow SHALL add triage for form and API submissions that are still open when fetched. It SHALL read the current title and map exact conventional prefixes bug or fix to bug, feat to enhancement, and docs to documentation, accepting optional scopes and breaking-change markers. Unknown prefixes SHALL add no category.

#### Scenario: API issue follows the bug form convention
- **WHEN** an API-created issue is titled `bug(accounts): quota is wrong`
- **THEN** the workflow adds triage and bug

#### Scenario: Existing form labels
- **WHEN** a form-created issue already has bug and triage
- **THEN** classification preserves those labels and every other existing label

#### Scenario: Unknown title
- **WHEN** an issue title does not match a recognized prefix
- **THEN** the workflow adds only triage

#### Scenario: Delayed run after closure
- **WHEN** an opened-event run fetches an issue that is already closed
- **THEN** it does not add labels

### Requirement: Metadata-only privileges

Classification workflows SHALL use SHA-pinned actions and only the permissions needed to read metadata and add their existing labels. They SHALL NOT check out or execute contributor code, interpolate author text into scripts, remove labels, grant review or approval labels, or change lifecycle status beyond initial issue triage.

#### Scenario: Approval text in a title
- **WHEN** an issue title contains script syntax or an approval label name
- **THEN** it cannot execute code or grant approval
- **AND** an existing simplicity-budget-approved label remains intact
