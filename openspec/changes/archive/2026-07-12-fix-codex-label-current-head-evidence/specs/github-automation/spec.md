## MODIFIED Requirements

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

## ADDED Requirements

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
