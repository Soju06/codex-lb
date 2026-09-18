## ADDED Requirements

### Requirement: CI strictly validates changed active OpenSpec folders

The required OpenSpec CI job MUST retain canonical capability validation and MUST strictly validate each surviving active change folder touched by the event diff. Pull requests MUST compare their head against its merge base with the event's target commit. Pushes MUST compare the event's before and after commits; merge queue events MUST compare their base and head commits. Missing or invalid comparison history MUST fail the job. A new branch push without a before commit MUST consider every tracked path at its head.

Changed paths MUST include both sides of renames and deleted files. Each surviving immediate folder under `openspec/changes/` MUST be validated once, except `archive`. Fully deleted folders and archived changes MUST be skipped. Unrelated active changes MUST NOT affect this validation. A strict validation failure MUST fail the required job.

#### Scenario: Malformed delta changes

- **WHEN** a pull request adds a malformed active change delta
- **THEN** strict validation fails the required OpenSpec job

#### Scenario: Target branch advances independently

- **GIVEN** an invalid active change exists only in newer target-branch history
- **WHEN** a pull request changes a valid active change from an older merge base
- **THEN** only the pull request's touched active folder is validated

#### Scenario: Delete or rename files

- **WHEN** a pull request deletes a file or moves it between active change folders
- **THEN** every surviving source and destination folder is validated once
- **AND** fully deleted folders and archive paths are skipped

#### Scenario: Missing comparison commit

- **WHEN** the event's comparison commit cannot be resolved
- **THEN** validation fails instead of reporting no changed folders

#### Scenario: Push or merge queue event

- **WHEN** a push or merge queue event touches an active change
- **THEN** the folder is strictly validated using the event's commit range
