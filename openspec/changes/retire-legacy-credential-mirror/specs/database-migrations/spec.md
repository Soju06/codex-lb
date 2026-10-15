## ADDED Requirements

### Requirement: Dropping the legacy dashboard credentials states the drain requirement and blocks nothing

Before applying any DDL, `run_upgrade()` — the single entry point shared by `python -m app.db.migrate upgrade`, the container entrypoint, the Helm pre-upgrade migration Job and `run_startup_migrations()` — MUST read the stamped revision with the existing side-effect-free inspection and decide whether the pending upgrade would cross the revision that drops the legacy dashboard credential columns. Whenever it would, and the database is not a fresh install, the upgrade MUST emit exactly one warning stating that replicas of any earlier release must be stopped first, because they map the dropped columns and their settings reads and credential mirrors both fail once the columns are gone; a fresh install, and an upgrade that stops short of the drop, MUST emit nothing. "Fresh install" MUST be decided by the ledger **and** the schema together — an empty ledger over a database that does not yet carry `dashboard_settings`, the table the dropped columns live in. An empty or absent `alembic_version` MUST NOT on its own be read as a fresh install: a ledger that was truncated, lost, or restored without its rows sits over an install that a replica of an earlier release may still be serving, and the replay it is about to run reaches the drop and removes those columns for real, so that database MUST be warned. `config.attributes["codex_lb_fresh_install"]` — the flag a published revision already reads to pick its fresh-install defaults — MUST NOT be reused as that signal, because it is false whenever an `alembic_version` table merely exists and would warn about a database with nothing to drain. The revision that drops the columns MUST descend from `20260909_020000_reproject_compat_admin_credentials`, so a database stamped anywhere below both reaches head in one command with its dashboard credentials copied onto the account rows before the columns they came from disappear. That ordering MUST be asserted by a test rather than re-checked at run time, and the upgrade MUST NOT be refused, gated behind a flag, an environment variable or a `Settings` field, or made conditional on the backend: `run_startup_migrations()` is the boot path, so refusing there would stop every install of an earlier release from starting, and the two-step upgrade it would demand applies exactly the same revisions in the same order. The warning MUST NOT be described as detecting a running previous-release replica: no such signal exists, and the guard proves only what the ledger and the schema say.

#### Scenario: A database from an older release upgrades in one step

- **GIVEN** a database stamped at a revision that precedes `20260909_020000_reproject_compat_admin_credentials`, whose legacy `dashboard_settings` columns still hold the dashboard password hash and TOTP counter
- **WHEN** the upgrade to head is run
- **THEN** it applies the whole chain in order and reaches head
- **AND** the bootstrap account row carries the password hash and TOTP counter the legacy columns held, and those columns are gone

#### Scenario: The drain requirement is stated by the process that performs it

- **GIVEN** a database that has already applied the reprojection and carries application data
- **WHEN** the upgrade crosses the drop revision
- **THEN** exactly one warning names the requirement to stop replicas of earlier releases first
- **AND** a fresh install applying the whole chain emits no such warning

#### Scenario: An upgrade that stops short of the drop says nothing

- **GIVEN** a database stamped below the drop revision
- **WHEN** the requested target is a revision below the drop
- **THEN** no drain warning is emitted and no DDL is refused

#### Scenario: A lost ledger over an existing install is not mistaken for a fresh one

- **GIVEN** an existing database carrying `dashboard_settings` and its credential columns, whose `alembic_version` has been lost — dropped altogether, or present but holding no rows
- **WHEN** the upgrade to head is run and replays the chain from base across the drop revision
- **THEN** the same single drain warning is emitted, because an empty ledger over an existing schema is a lost ledger rather than a new install
- **AND** an empty ledger over a database that does not yet carry `dashboard_settings` — a first run that created `alembic_version` and died before applying anything — still emits nothing

#### Scenario: A relative or abbreviated target is resolved before the decision

- **GIVEN** a database stamped at the direct parent of the drop revision
- **WHEN** the upgrade target is given as a relative specifier such as `+1`, or as an unambiguous revision-id prefix, rather than as `head` or a full id
- **THEN** the same single warning is emitted, because the decision is taken over the revisions Alembic will actually apply rather than over the target string

### Requirement: A ledger behind a schema that already retired the legacy credentials is re-stamped, not replayed

`run_upgrade()` MUST NOT replay the revision chain over a database that has already dropped the legacy dashboard credential columns. When `runtime_sentinels` carries the retirement marker written by the drop revision and the Alembic ledger does not include that revision — because `alembic_version` is absent or empty, or because it names a revision that is neither the drop revision nor a descendant of it — `run_upgrade()` MUST stamp the ledger at the drop revision before applying anything, MUST log exactly one warning naming the marker and what the ledger said, and MUST then continue to the requested target from there. Only the drop revision writes that marker and its downgrade removes it again, so the marker means the columns are gone whatever the ledger says: a ledger that disagrees has been lost, rewound or restored from a partial backup, and it is the ledger that is wrong.

The replay is not merely wasteful, it is destructive, which is why this is a requirement rather than an optimisation: `20260213_000600_add_dashboard_settings_totp` and `20260213_000700_add_dashboard_settings_password` re-add the three columns to an existing `dashboard_settings` as **empty** columns, and `20260909_020000_reproject_compat_admin_credentials` then reads that emptiness as "the previous release removed the password" and clears the credential on the bootstrap account row — which after this release is the only copy, so the install is locked out of its own dashboard. A ledger rewound past the column re-creation fails outright instead, inside `20260909_010000_add_dashboard_users`. The marker MUST be the signal, because the ledger is the thing that went missing; and the published revisions above MUST NOT be edited to defend themselves, because an install that already applied them never applies them again.

The re-stamp MUST be keyed on that evidence alone. A database carrying no such marker MUST keep today's behaviour and replay the chain, so a genuinely fresh install and a pre-drop ledger-less schema are unaffected; and a ledger naming a revision this build cannot resolve MUST be left alone, so the existing legacy-id repair and its error keep working unchanged.

#### Scenario: A ledger-less post-drop database keeps its credentials

- **GIVEN** a database that has already applied the drop revision, whose bootstrap account holds a password hash and a TOTP secret, and whose `alembic_version` table has been lost
- **WHEN** `run_upgrade()` is asked for head
- **THEN** it stamps the ledger at the drop revision, warns once that it did so, and reaches head
- **AND** the account row still carries the password hash and TOTP secret it had, and the three legacy columns are still absent

#### Scenario: A rewound ledger over a retired schema is re-stamped

- **GIVEN** the same database with an `alembic_version` naming a revision below the drop
- **WHEN** `run_upgrade()` is asked for head
- **THEN** the ledger is stamped forward to the drop revision rather than the chain being replayed over the schema
- **AND** the upgrade reaches head with the account row's credential intact

#### Scenario: A database without the marker still replays

- **GIVEN** a database with no `alembic_version` table and no retirement marker
- **WHEN** `run_upgrade()` is asked for head
- **THEN** the chain is applied from base as before and nothing is stamped ahead of it
