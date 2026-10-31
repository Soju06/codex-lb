## ADDED Requirements

### Requirement: Dashboard explains routing observations and soft controls

Request details MUST retain and display available affinity source, kind and
hash with the historical selected account. This observation MUST be labelled
as historical rather than current live ownership. Accounts MUST show the
configured selection strategy and selected account's routing policy with an
explanation that sticky ownership, model/key scope, health and quota can
constrain selection. The sticky-session UI MUST offer an explicit account
picker only for supported soft mappings and explain that hard continuations
and running requests cannot be moved by this control. Failures MUST be shown
without a success notification.

#### Scenario: Operator inspects an unsuccessful request

- **WHEN** the operator opens request details
- **THEN** available logged account and affinity details remain visible
- **AND** the UI does not claim these are the current session owner

#### Scenario: Operator switches a soft mapping

- **WHEN** an operator selects another account for a prompt-cache mapping
- **THEN** the UI sends the viewed expected owner and reloads mappings
- **AND** a conflict asks the operator to reload rather than overwriting

### Requirement: Per-account runtime observations do not change selection

An authorized dashboard operator MUST be able to inspect a selected account's
replica-local routing health tier, recent error count, last selection time,
active stream/create pressure and cooldown/backoff deadlines. The observation
MUST use a detached snapshot, MUST NOT run account selection, acquire a lease,
or reset health, and MUST identify its replica-local scope. It MUST NOT claim
to establish eligibility for a particular model, API key or hard continuation.
Missing or deleting accounts MUST return not found.

#### Scenario: Account has a temporary routing backoff

- **WHEN** an operator opens the account's routing observation
- **THEN** the current replica's deadline and recent error count are visible
- **AND** the read does not change that account's next selection or health
