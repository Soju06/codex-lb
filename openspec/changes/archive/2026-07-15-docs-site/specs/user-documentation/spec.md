## ADDED Requirements

### Requirement: Documentation site builds strictly and deploys from main

The repository SHALL contain a mkdocs-material documentation site (`mkdocs.yml` with `docs_dir: docs`) published at https://soju06.github.io/codex-lb/. A dedicated GitHub Actions workflow SHALL build the site with `mkdocs build --strict` on every pull request that touches docs inputs and on pushes to `main`, and SHALL deploy to GitHub Pages only for non-pull-request events on `main`. The deploy job MUST use least-privilege permissions (`pages: write`, `id-token: write` scoped to the deploy job) and MUST NOT cancel in-flight deploys.

#### Scenario: PR with a broken internal docs link fails the build

- **GIVEN** a pull request editing a page under `docs/` with a link to a nonexistent page or anchor
- **WHEN** the Docs workflow runs
- **THEN** `mkdocs build --strict` fails the build check
- **AND** no Pages deploy is attempted for the pull request

#### Scenario: Push to main deploys the site

- **WHEN** a commit touching `docs/**` or `mkdocs.yml` lands on `main`
- **THEN** the workflow builds the site strictly, uploads the Pages artifact, and deploys it to the `github-pages` environment

### Requirement: Docs pages link their governing OpenSpec capability

OpenSpec remains the normative source of truth. Every docs page that documents spec-governed behavior SHALL carry a link to the governing `openspec/specs/<capability>/` location on GitHub, and docs pages MUST NOT introduce requirements or behavior claims absent from OpenSpec.

#### Scenario: Behavior page carries a spec link

- **WHEN** a reader opens a docs page describing spec-governed behavior (e.g. routing strategies)
- **THEN** the page contains a link to the governing capability under `openspec/specs/` on GitHub

### Requirement: README stays a quickstart

`README.md` SHALL remain a slim quickstart: hero screenshots, feature summary, Quick Start, a single in-README client configuration path (Codex CLI) with a table linking other clients to the docs site, configuration and data pointers, documentation links, and development notes. Detailed operational content (auth modes, routing guide, database runbooks, Kubernetes, remote setup, per-client walkthroughs) SHALL live on the documentation site instead. The README SHALL carry a prominent link to the documentation site and SHALL keep the all-contributors generated block between its `ALL-CONTRIBUTORS-LIST` markers. `README.zh-CN.md` SHALL carry a banner identifying the English documentation site as canonical.

#### Scenario: Moved content is reachable from the README

- **WHEN** a reader looks for content removed from the README (e.g. the Postgres 16→18 upgrade runbook)
- **THEN** the README links to the documentation site where that content now lives

#### Scenario: Contributors block survives the diet

- **WHEN** the all-contributors bot regenerates the contributors table
- **THEN** the `ALL-CONTRIBUTORS-LIST:START`/`:END` markers still exist in `README.md` and the update applies cleanly

### Requirement: .env.example is a commented zero-drift sample

`.env.example` SHALL contain only commented-out values, SHALL NOT state values that contradict the code defaults in `app/core/config/settings.py`, and SHALL retain the commented `# CODEX_LB_LEADER_ELECTION_ENABLED=false` single-instance escape hatch. Copying the file verbatim MUST yield the same behavior as running with no configuration.

#### Scenario: Copying the sample changes nothing

- **GIVEN** a fresh install
- **WHEN** the operator copies `.env.example` to `.env.local` without uncommenting anything
- **THEN** the application starts with identical effective settings to a no-.env install
- **AND** leader election remains enabled

#### Scenario: Leader-election escape hatch stays documented

- **WHEN** `.env.example` is read
- **THEN** it contains the commented line `# CODEX_LB_LEADER_ELECTION_ENABLED=false`
- **AND** no active (uncommented) assignment disables leader election
