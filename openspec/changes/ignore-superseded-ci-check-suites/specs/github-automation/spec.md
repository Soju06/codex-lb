## ADDED Requirements

### Requirement: Codex review labels use the authoritative current-head CI suite

The Codex review label synchronizer SHALL identify the CI workflow from the
most recent `CI Required` check and SHALL treat the newest same-head run of
that workflow (ordered by workflow-run creation time, then check recency, then
run id) as the authoritative CI suite when multiple runs of the same GitHub
Actions CI workflow exist for one pull-request head, even when that run has
not yet produced its own `CI Required` check. It MUST ignore Actions checks —
including stale required contexts — only from superseded (older) runs of that
workflow, while checks from the authoritative run, checks that cannot be
attributed to a workflow run, non-Actions status evidence, and failures from
independent workflows remain blocking evidence.

#### Scenario: Cancelled duplicate leaves a unique failed placeholder

- **GIVEN** an older CI workflow run for the current head was cancelled
- **AND** that run left a uniquely named non-required matrix placeholder in failure
- **AND** a newer run for the same head completed every required check including `CI Required` successfully
- **WHEN** Codex review labels are synchronized
- **THEN** the stale placeholder does not make the current head failed
- **AND** the synchronizer may request or accept current-head Codex review evidence

#### Scenario: Authoritative CI run has an optional failure

- **GIVEN** the newest run of the CI workflow identified by the latest `CI Required` check is the authoritative run
- **AND** another check in that same run failed
- **WHEN** Codex review labels are synchronized
- **THEN** the current head remains classified as failed

#### Scenario: A newer run stays pending until its own CI Required completes

- **GIVEN** an older run of the CI workflow completed `CI Required` successfully for the current head
- **AND** a newer run of the same CI workflow was created for the same head
- **AND** the newer run has started early checks but has not yet completed its own `CI Required` check
- **WHEN** Codex review labels are synchronized
- **THEN** the newer run is the authoritative CI suite and the older run's completed checks are ignored
- **AND** the current head remains classified as pending until the newer run's `CI Required` completes

#### Scenario: Independent workflow on the same head fails

- **GIVEN** the authoritative CI workflow run is successful
- **AND** a different GitHub Actions workflow has a failed check on the same head
- **WHEN** Codex review labels are synchronized
- **THEN** the independent workflow failure remains blocking
