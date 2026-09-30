## ADDED Requirements

### Requirement: The upgrade order for the credential-column drop is documented

The documentation SHALL state the upgrade contract of the release that drops the legacy dashboard credential columns, in the operator's own words and not only in a spec. `docs/deployment/kubernetes.md` SHALL carry the sequence: replicas of any earlier release MUST be stopped before the migration runs, because the chart's migration Job is a `pre-upgrade` hook that commits its DDL while the previous release's pods are still serving, and those pods map the dropped columns — their settings reads fail and, on the release immediately before this one, their credential mirror writes fail too. It SHALL name the concrete ways to achieve that (blue/green with the old colour stopped, taking the replica count to zero, or disabling the Job and running `python -m app.db.migrate upgrade` by hand after the stop) and SHALL state that a stock rolling `helm upgrade` of a multi-replica install is not a supported path for this release. It SHALL state that **rollback is supported to the immediately preceding release only**: the downgrade re-creates the columns and re-projects them from the bootstrap account row, which an older build would read as its credential, and when that account has been deleted an older build reads the empty columns as a never-bootstrapped install. `docs/troubleshooting.md` SHALL carry the symptom an operator who skipped the stop actually sees — an earlier-release replica failing its settings reads against the post-drop schema — and SHALL say that the remedy is to finish the roll rather than to re-run the migration, since the upgrade itself refuses nothing. `docs/authentication.md` SHALL stop promising the retired behaviour: the sign-in paragraph MUST drop the clause that says "Require TOTP on login" waits for the migrated account (`409 compat_user_locked`), the accounts paragraph MUST drop the clause that says the migrated account keeps its role and status and cannot be deleted, and both MUST say instead that the migrated account is now ordinary and may be renamed from the People tab while the name `admin` itself stays reserved. `docs/sso.md` SHALL keep working for a renamed install: its recovery commands MUST show the username as an argument the operator substitutes rather than assume the bootstrap account is still called `admin`.

#### Scenario: The operator meets the order before the upgrade

- **WHEN** an operator planning the upgrade reads the Kubernetes deployment page
- **THEN** it states that earlier-release replicas must be stopped first, gives at least one concrete way to do it, and says a stock rolling upgrade is unsupported for this release

#### Scenario: The consequence of skipping the stop is findable

- **WHEN** an operator whose earlier-release pods started failing their settings reads searches the troubleshooting page
- **THEN** the page names the dropped columns as the cause, says the migration is complete and must not be re-run, and points at the deployment page's stop-first sequence

#### Scenario: The pages no longer promise the lock

- **WHEN** the authentication page is read after this change
- **THEN** it mentions neither `compat_user_locked` nor a migrated account that cannot be deleted, and it says the bootstrap account can be renamed while the name `admin` stays reserved

#### Scenario: The recovery runbook survives a rename

- **WHEN** an operator whose bootstrap account was renamed follows the recovery page
- **THEN** every command shows the account name as a substitutable argument and the page builds with `mkdocs build --strict`
