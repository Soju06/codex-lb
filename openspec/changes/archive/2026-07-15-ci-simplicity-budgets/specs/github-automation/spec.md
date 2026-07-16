## ADDED Requirements

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
