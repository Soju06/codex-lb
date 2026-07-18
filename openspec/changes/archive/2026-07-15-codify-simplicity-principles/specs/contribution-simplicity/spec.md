# contribution-simplicity

## ADDED Requirements

### Requirement: New features default to off or zero-config

A PR that introduces a new feature MUST NOT add a required setup step (environment variable, migration action, external account, or manual file edit) to the base install path. The feature SHALL either default to off or work with a zero-config default, and `docker run` / `uvx codex-lb` without an env file SHALL continue to produce a working proxy and dashboard. A change that genuinely cannot avoid a new required setup step SHALL carry the maintainer-applied `simplicity-budget-approved` label before merge.

#### Scenario: Feature ships with a working default

- **WHEN** a PR introduces a new feature without any new required setup step
- **THEN** the base install path (`docker run` / `uvx codex-lb` with no env file) still produces a working proxy and dashboard
- **AND** the PR passes this gate without a label

#### Scenario: Unavoidable setup step needs maintainer approval

- **GIVEN** a PR whose feature cannot work without a new required setup step
- **WHEN** the PR is evaluated for merge
- **THEN** it is blocked until a maintainer applies the `simplicity-budget-approved` label

### Requirement: New settings justify not being a default

A PR that adds a `CODEX_LB_*` setting or an `.env.example` entry MUST include, in the PR body, a justification of why the value cannot be a hardcoded default. Settings that only tune internals SHOULD NOT be added to `.env.example`.

#### Scenario: Setting added with justification

- **WHEN** a PR adds a new `CODEX_LB_*` setting and its PR body names the setting with a why-not-a-default justification
- **THEN** the PR passes this gate

#### Scenario: Setting added without justification

- **WHEN** a PR adds a new `CODEX_LB_*` setting or `.env.example` line and the PR body carries no justification for it
- **THEN** the reviewer blocks the PR until the justification is added or the setting is replaced by a default

### Requirement: README, configuration, and dashboard surface are budgeted

README top-level sections, the `.env.example` surface, and dashboard core-navigation items SHALL be capped by the budget values defined in `.github/simplicity-budgets.toml` (introduced by the `ci-simplicity-budgets` change; reviewer-enforced until that lands). A PR that exceeds a budget SHALL be blocked from merge unless a maintainer applies the `simplicity-budget-approved` label. Raising a budget value SHALL itself require the label plus an OpenSpec change.

#### Scenario: PR stays within budgets

- **WHEN** a PR leaves README top-level sections, `.env.example`, and dashboard core-nav items within the budgets in `.github/simplicity-budgets.toml`
- **THEN** the PR passes this gate with no label required

#### Scenario: Budget exceeded without the override label

- **WHEN** a PR pushes a budgeted surface over its cap and no `simplicity-budget-approved` label is present
- **THEN** the PR is blocked from merge

#### Scenario: Budget exceeded with maintainer approval

- **GIVEN** a PR that exceeds a budget
- **WHEN** a maintainer applies the `simplicity-budget-approved` label
- **THEN** the PR may merge, and the exception is visible in the PR's label history

### Requirement: Feature documentation lives in docs plus OpenSpec, not new README sections

New feature or behavior documentation SHALL be added under `docs/` (the user-facing rendering) and MUST link back to the owning `openspec/specs/<capability>/` entry, which remains the source of truth. A PR SHALL NOT add a new README top-level section to document a feature; doing so is a budget exception under the budget requirement, not a documentation mechanism.

#### Scenario: Feature documented in the docs site

- **WHEN** a PR documents a new feature with a page under `docs/` that links back to the owning `openspec/specs/<capability>/` entry
- **THEN** the PR passes this gate

#### Scenario: Feature documented as a new README section

- **WHEN** a PR adds a new README top-level section to document a feature
- **THEN** the reviewer blocks the PR unless it carries the `simplicity-budget-approved` label, and the content is redirected to `docs/`

### Requirement: Dashboard-visible changes include screenshots

A PR that changes what the dashboard renders MUST include before/after screenshots (or a short screen recording) in the PR body before merge.

#### Scenario: Dashboard change with screenshots

- **WHEN** a PR alters dashboard rendering and its body contains before/after screenshots
- **THEN** the PR passes this gate

#### Scenario: Dashboard change without screenshots

- **WHEN** a PR alters dashboard rendering and its body contains no screenshots or recording
- **THEN** the reviewer blocks the PR until visual evidence is attached
