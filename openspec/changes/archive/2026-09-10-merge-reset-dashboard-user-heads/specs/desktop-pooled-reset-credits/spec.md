## ADDED Requirements

### Requirement: Reset pooling composes with dashboard users without state loss

The database MUST upgrade to one head from the published reset/guest merge and dashboard audit-attribution revision. Existing audit rows, roles, grants, users, identities, credentials, user and guest generations, reset bindings, reset policy and explicit retention settings MUST survive. Missing user schema MUST receive main's existing credential backfill and preset roles. Merge-only downgrade and re-upgrade MUST retain both parent schemas and their data. Existing authorization and CSRF rules MUST remain unchanged.

#### Scenario: Populated reset database adopts dashboard users

- **WHEN** a reset database with credentials and bound credits upgrades to the merge
- **THEN** main's role and user migrations run with their original credential semantics
- **AND** exact reset bindings and existing settings remain unchanged

#### Scenario: Populated user database adopts reset pooling

- **WHEN** a database with custom grants, identities and nonzero session generations upgrades
- **THEN** all existing rows remain unchanged and pooling defaults off
- **AND** merge-only downgrade retains both parent stamps and data
