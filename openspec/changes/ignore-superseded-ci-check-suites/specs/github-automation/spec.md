## ADDED Requirements

### Requirement: Codex review labels use the authoritative current-head CI suite

The Codex review label synchronizer SHALL treat the run containing the most
recent `CI Required` check as the authoritative CI suite when multiple GitHub
Actions CI workflow runs exist for one pull-request head. It MUST ignore
non-required Actions checks from superseded runs, while required check contexts,
non-Actions status evidence, and failures from the authoritative run remain
blocking.

#### Scenario: Cancelled duplicate leaves a unique failed placeholder

- **GIVEN** an older CI workflow run for the current head was cancelled
- **AND** that run left a uniquely named non-required matrix placeholder in failure
- **AND** a newer run for the same head completed every required check including `CI Required` successfully
- **WHEN** Codex review labels are synchronized
- **THEN** the stale placeholder does not make the current head failed
- **AND** the synchronizer may request or accept current-head Codex review evidence

#### Scenario: Authoritative CI run has an optional failure

- **GIVEN** the most recent `CI Required` check identifies the authoritative workflow run
- **AND** another check in that same run failed
- **WHEN** Codex review labels are synchronized
- **THEN** the current head remains classified as failed
