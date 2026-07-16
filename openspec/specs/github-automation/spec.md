# github-automation Specification

## Purpose
Repository automation around the Codex review merge gate: the `Codex review labels` workflow and its synchronization script keep `🤖 codex: ok` / `🤖 codex: needs work` labels faithful to current-head CI state and Codex review evidence, with token sourcing that stays within GitHub API quotas and degrades safely when privileged credentials are unavailable.
## Requirements
### Requirement: Codex review label sync write-token fallback

The `Codex review labels` workflow MUST execute the label synchronization script from the trusted default branch and MUST prefer a dedicated GitHub App installation token, then a repository-provided write token, before falling back to the default `github.token`.

#### Scenario: GitHub App credentials are configured

- **WHEN** the repository defines the `CODEX_LABEL_SYNC_APP_ID` variable and the `CODEX_LABEL_SYNC_APP_PRIVATE_KEY` secret
- **THEN** the workflow mints a short-lived installation token for that App before the sync step
- **AND** the mint requests only the label-sync permission subset (actions write, checks read, contents read, issues write, pull requests read, statuses read) rather than inheriting all installation permissions
- **AND** the sync step uses that token ahead of `CODEX_LABEL_SYNC_TOKEN`, `RELEASE_PLEASE_TOKEN`, and `github.token`

#### Scenario: App token mint fails or is not configured

- **WHEN** the mint step fails, or the `CODEX_LABEL_SYNC_APP_ID` variable is absent
- **THEN** the job does not fail because of the mint step
- **AND** the sync step falls back to the next available token in the chain

#### Scenario: Privileged token is configured

- **WHEN** the workflow synchronizes Codex review labels and no App token was minted
- **THEN** it uses `CODEX_LABEL_SYNC_TOKEN` when present
- **AND** it falls back to `RELEASE_PLEASE_TOKEN` before `github.token`
- **AND** it checks out the default branch with persisted checkout credentials disabled

### Requirement: Codex review label sync write-denial resilience

The Codex label synchronization script MUST distinguish GitHub write-permission denials from classification/read failures.

#### Scenario: GitHub App token cannot mutate a PR resource

- **WHEN** a label, comment, or workflow-run approval write returns `Resource not accessible by integration (HTTP 403)`
- **THEN** the workflow logs a per-PR warning for the skipped mutation
- **AND** it continues processing remaining selected PRs
- **AND** it exits successfully if no read/classification errors occurred

#### Scenario: PR state cannot be read or classified

- **WHEN** the script cannot read required PR state, check state, merge state, or Codex review evidence
- **THEN** the workflow fails rather than silently treating the PR as synchronized

### Requirement: Codex review label sync review-thread state

The Codex label synchronization script MUST grant `🤖 codex: ok` only when the
current pull-request head has green required checks, a clean Codex review for
that head, and no unresolved current-head Codex finding threads. It MUST treat
unresolved, non-outdated Codex inline review findings on the current head as
needs-work evidence, and MUST NOT treat inline Codex findings from resolved or
outdated review threads as active needs-work evidence. It MUST attribute an
unresolved thread to the current head only when the thread's current commit,
original commit, or body text ties it to the current head, and MUST treat
stale unresolved Codex inline threads as non-blocking when none of those tie
them to the current head.

#### Scenario: Resolved inline finding no longer blocks the ok label

- **WHEN** a current-head inline Codex finding comment belongs to a resolved
  review thread
- **AND** a clean current-head Codex review exists
- **THEN** the script does not classify that inline finding as active
  needs-work evidence

#### Scenario: Unresolved inline finding still blocks the ok label

- **WHEN** a current-head inline Codex finding comment belongs to an unresolved,
  non-outdated review thread
- **THEN** the script classifies that inline finding as active needs-work
  evidence

#### Scenario: stale rebased inline thread remains unresolved

- **GIVEN** a pull request was rebased after a Codex inline finding
- **AND** the unresolved GraphQL review thread still reports `isOutdated=false`
- **AND** the thread's current commit is not the current head
- **AND** the thread's original commit is not the current head
- **AND** the thread body does not mention the current head
- **WHEN** the label synchronizer evaluates the pull request
- **THEN** that thread does not force `🤖 codex: needs work`

#### Scenario: reanchored unresolved inline thread belongs to the current head

- **GIVEN** an unresolved Codex inline finding thread
- **AND** the thread's current commit is the pull request head
- **AND** the thread's original commit is older than the pull request head
- **WHEN** the label synchronizer evaluates the pull request
- **THEN** that thread blocks `🤖 codex: ok`
- **AND** the synchronizer records a needs-work reason that links to the thread

#### Scenario: unresolved inline thread belongs to the current head

- **GIVEN** an unresolved Codex inline finding thread
- **AND** the thread's original commit is the pull request head
- **WHEN** the label synchronizer evaluates the pull request
- **THEN** that thread blocks `🤖 codex: ok`
- **AND** the synchronizer records a needs-work reason that links to the thread

#### Scenario: unresolved inline thread mentions the current head explicitly

- **GIVEN** an unresolved Codex inline finding thread
- **AND** the thread body mentions the current pull request head
- **WHEN** the label synchronizer evaluates the pull request
- **THEN** that thread blocks `🤖 codex: ok`
- **AND** the synchronizer records a needs-work reason that links to the thread

#### Scenario: resolved inline thread is resynchronized by the scheduled fallback

- **GIVEN** a pull request has a `🤖 codex: needs work` label from an unresolved Codex inline finding
- **WHEN** that review thread is resolved
- **THEN** the scheduled Codex label synchronization run resynchronizes the open pull request's labels

### Requirement: Codex review labels use the authoritative current-head CI suite

The Codex review label synchronizer SHALL identify the CI workflow from the
most recent `CI Required` check and SHALL treat the newest same-head run of
that workflow (ordered by the current attempt's start time, falling back to
workflow-run creation time, then check recency and run id) as the authoritative
CI suite when multiple runs of the same GitHub Actions CI workflow exist for
one pull-request head, even when that run has not yet produced its own
`CI Required` check. It MUST ignore Actions checks —
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

#### Scenario: An older workflow run id is manually rerun

- **GIVEN** a newer-created CI workflow run completed successfully for the current head
- **AND** an older workflow `run_id` is manually rerun afterward
- **WHEN** the older run's new attempt has the latest `run_started_at`
- **THEN** that rerun is the authoritative CI suite
- **AND** its pending or failed checks remain blocking evidence

#### Scenario: Independent workflow on the same head fails

- **GIVEN** the authoritative CI workflow run is successful
- **AND** a different GitHub Actions workflow has a failed check on the same head
- **WHEN** Codex review labels are synchronized
- **THEN** the independent workflow failure remains blocking

### Requirement: Codex label sync MUST use check-run recency evidence

When multiple check runs have the same context name on a pull-request head, the label synchronizer MUST classify the current context from the newest run by
start or creation time. Completion time MUST NOT let an older superseded run
override a newer rerun that has already started.

#### Scenario: older duplicate run completes after a newer rerun starts

- **GIVEN** two check runs share the same name
- **AND** the older run started first but completes after the newer run starts
- **WHEN** the label synchronizer deduplicates check runs
- **THEN** it keeps the newer run
- **AND** a pending newer run keeps the pull request check state pending instead of failed

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

