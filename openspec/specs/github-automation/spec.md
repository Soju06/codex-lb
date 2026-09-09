# github-automation Specification

## Purpose
Repository automation around the Codex review merge gate: the `Codex review labels` workflow and its synchronization script keep `🤖 codex: ok` / `🤖 codex: needs work` labels faithful to current-head CI state and Codex review evidence, keep `needs rebase` faithful to confirmed merge-conflict state, and use token sourcing that stays within GitHub API quotas and degrades safely when privileged credentials are unavailable.
## Requirements
### Requirement: CI required check contexts remain stable under path filtering

The CI workflow SHALL create every branch-protection-required check context for
pull requests even when path filters determine that the expensive implementation
for a subsystem is unrelated to the change.

#### Scenario: non-backend pull request still creates pytest matrix contexts

- **GIVEN** a pull request changes no backend paths
- **AND** the repository ruleset requires `Tests (pytest, unit)`, `Tests (pytest, integration-core)`, `Tests (pytest, integration-bridge)`, and `Tests (pytest, e2e)`
- **WHEN** the CI workflow runs
- **THEN** each required pytest matrix check context is created
- **AND** each context completes successfully via a placeholder step
- **AND** the real pytest setup and test commands are skipped for that non-backend change

#### Scenario: backend pull request runs the real pytest slices

- **GIVEN** a pull request changes backend paths
- **WHEN** the CI workflow runs
- **THEN** each required pytest matrix check context runs its corresponding `make test-*` target
- **AND** the placeholder step is skipped

### Requirement: Simplicity budgets are enforced mechanically in CI

The repository SHALL define simplicity budgets in `.github/simplicity-budgets.toml`
covering at least: `README.md` maximum line count (counted after removing the
generated `ALL-CONTRIBUTORS-LIST` block, marker lines inclusive), `README.md`
maximum top-level heading count (h1 and h2 only, with lines inside fenced code
blocks excluded), `.env.example` maximum line count, and the dashboard
core-navigation maximum item count together with the source file and array
name it is read from. A dedicated `Simplicity budgets` workflow, separate from
the main CI workflow, SHALL run `.github/scripts/check_simplicity_budgets.py`
(stdlib-only) on pull requests (including `labeled`/`unlabeled` events),
pushes to `main`, and `merge_group` events, and the check SHALL fail when any
measured value exceeds its budget. Budget increases are made by editing
`.github/simplicity-budgets.toml`, which keeps every exceedance a reviewable
diff (see the `contribution-simplicity` capability for the governing
principles).

#### Scenario: README grows past its line budget

- **GIVEN** a pull request whose `README.md`, after removing the
  `ALL-CONTRIBUTORS-LIST` block, exceeds `readme.max_lines`
- **WHEN** the Simplicity budgets workflow runs
- **THEN** the check prints the measured and budgeted values, emits an error
  annotation for `README.md`, and exits non-zero

#### Scenario: Shell comments inside fenced code blocks are not headings

- **GIVEN** a `README.md` containing `# comment` lines inside fenced code
  blocks
- **WHEN** the checker counts top-level headings
- **THEN** lines inside fenced code blocks are not counted
- **AND** fences using ` ``` ` or `~~~`, indented up to 3 spaces, are
  recognized, and a fence is closed only by a fence line using the same
  character
- **AND** only h1 and h2 heading lines outside fences count against
  `readme.max_top_level_headings`

#### Scenario: All budgets within limits

- **WHEN** every measured value is at or below its configured budget
- **THEN** the check prints each metric as `actual/budget OK` and exits 0

### Requirement: Nav budget refuses to pass when its target disappears

The budget checker MUST exit with a distinct configuration-error status
(exit code 2) and an explicit repoint instruction when the navigation source
file or the configured navigation array named in
`.github/simplicity-budgets.toml` `[core_nav]` cannot be found. A refactor
that moves or renames the navigation array MUST update `[core_nav]` in the
same change for the check to pass. The checker MUST use the same exit code 2
when `.github/simplicity-budgets.toml` itself is missing or malformed, and
when a `README.md` `ALL-CONTRIBUTORS-LIST:START` marker has no matching `END`
marker (an unclosed block would otherwise silently exclude the rest of the
file from the budget).

#### Scenario: Nav array renamed without repointing the manifest

- **GIVEN** the configured nav array no longer exists in the configured file
- **WHEN** the checker runs
- **THEN** it exits with code 2
- **AND** the error message names the missing array and instructs updating
  `.github/simplicity-budgets.toml` in the same pull request

### Requirement: Simplicity budget override label

The checker SHALL read PR label names from the `PR_LABELS` environment
variable, a JSON array the workflow resolves by querying the pull request's
current labels from the GitHub API at run time (never from the event
payload, which can be stale or empty on fork pull requests and re-runs).
When the `simplicity-budget-approved` label is present, budget violations
SHALL be downgraded to warning annotations and the check SHALL exit 0 while
still printing every measured metric. The workflow MUST trigger on `labeled`
and `unlabeled` so that toggling the label re-evaluates the check
immediately, and the failure message MUST explain the override path,
including that re-running a failed run after labeling also works because
labels are fetched live. Push and merge-group runs carry no pull-request
labels, so the override MUST NOT apply there: a change that would leave
`main` over budget MUST raise the budget in
`.github/simplicity-budgets.toml` in the same diff.

#### Scenario: Maintainer approves a temporary exceedance

- **GIVEN** a pull request over budget
- **WHEN** a maintainer applies the `simplicity-budget-approved` label
- **THEN** the `labeled` event starts a fresh check run that resolves the
  live label set from the API and sees the label (as would a manual re-run
  of the failed run)
- **AND** the run reports the violations as warning annotations and exits 0

#### Scenario: Label override does not launder main

- **GIVEN** a run triggered by a push to `main` or a `merge_group` event
- **WHEN** a budget is exceeded
- **THEN** no pull-request label set is resolved and the check fails
  regardless of any label on the originating pull request

### Requirement: Automated review summaries avoid PR-body CI restarts

Repository CodeRabbit configuration MUST place generated high-level summaries
in the walkthrough comment rather than automatically updating the PR body.
Summary generation and automated review MUST remain enabled. Release evidence
edits MUST still be revalidated by the release guards workflow on the
`pull_request` `edited` event; this optimization MUST NOT skip or manufacture
successful required CI checks.

#### Scenario: Review completes after a push

- **WHEN** CodeRabbit generates its automatic high-level summary
- **THEN** its configured destination is the walkthrough comment
- **AND** summary generation does not require an automatic PR-body update

#### Scenario: Release evidence is edited

- **WHEN** a maintainer edits a release PR's validation evidence
- **THEN** the release guards workflow re-runs for that pull request
- **AND** the CI matrix workflow does not start a new run for the unchanged head

### Requirement: PR metadata edits do not restart the CI matrix

The CI matrix workflow (`ci.yml`) MUST NOT subscribe to the `pull_request`
`edited` event. Jobs that read pull-request metadata (title, body, head branch)
MUST live in a separate lightweight workflow with its own concurrency group
that subscribes to `opened`, `reopened`, `synchronize`, `edited`, and
`ready_for_review`. The split MUST keep every check context name unchanged.
The matrix workflow MUST NOT achieve this by keeping `edited` and skipping jobs
conditionally, because skipped check runs satisfy branch protection and a
body-only edit could then mint passing checks for a red or untested head.

#### Scenario: PR description is edited while the matrix is running

- **GIVEN** a CI matrix run is in progress for a pull request head
- **WHEN** the pull request title or body is edited
- **THEN** no new CI matrix run is created and the in-progress run is not cancelled
- **AND** the release guards workflow runs against the edited metadata

#### Scenario: New commits still run the full matrix

- **WHEN** a pull request receives new commits
- **THEN** the CI matrix workflow runs for the new head
- **AND** the superseded run for the same ref is cancelled by the concurrency group

#### Scenario: Release guard contexts keep their names

- **WHEN** the release guards run from their own workflow
- **THEN** they report as `Beta release guard` and `Stable release guard`
- **AND** the branch ruleset's required contexts are unchanged

