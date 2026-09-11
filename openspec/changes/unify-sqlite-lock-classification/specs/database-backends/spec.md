## ADDED Requirements

### Requirement: SQLite write-lock contention is classified in one place

Every site that decides whether a failure is transient SQLite write-lock contention MUST reach that decision through one shared predicate rather than its own substring match, so a message SQLite emits for contention is never transient in one module and fatal in another. That predicate MUST accept only an `OperationalError`, MUST inspect the DRIVER exception rather than the rendered statement and bound parameters (the rendered form would classify an unrelated failure as transient whenever the SQL or a parameter value contained the lock text), and MUST match SQLITE_BUSY's `database is locked` and `database is busy`, SQLITE_LOCKED's `database table is locked` and `database schema is locked`, and a `busy_snapshot` extended result-code name appearing in the message.

Wherever such a failure is reported, retried, or swallowed, the report MUST name the driver's extended result code (`sqlite_errorname`, or its absence) alongside an identifier for the write. `database is locked` is emitted both for an instant `SQLITE_BUSY_SNAPSHOT`, where retrying on a fresh transaction is the whole fix, and for a `SQLITE_BUSY` returned only after the full busy timeout, where another writer held the slot that long and no retry budget helps; without the result-code name an occurrence cannot be told apart after the fact. These reports MUST NOT include the values being written.

Unifying the predicate MUST NOT change any site's control flow: a site that retries on its own bounded budget keeps that budget, its own backoff and its own re-raise, and a site that deliberately fails fast so its next tick retries MUST keep failing fast.

#### Scenario: One classification serves every call site

- **GIVEN** a SQLite failure whose driver message is `database table is locked`
- **WHEN** any site that classifies write-lock contention inspects it
- **THEN** every such site treats it as transient contention
- **AND** no site carries its own list of lock messages

#### Scenario: Lock text in the statement or parameters is not contention

- **GIVEN** an `OperationalError` whose driver message is an unrelated failure but whose bound parameters contain the text `database is locked`
- **WHEN** the shared predicate classifies it
- **THEN** it is not treated as transient write-lock contention
- **AND** it propagates with the call site's existing non-lock outcome

#### Scenario: A reported lock failure names its mechanism

- **GIVEN** a write that loses the SQLite writer slot and whose driver set `sqlite_errorname`
- **WHEN** the failure is retried, swallowed, or reported as an exhausted budget
- **THEN** the report names the write and the driver's extended result code, or records its absence
- **AND** the report contains none of the values the write was persisting
