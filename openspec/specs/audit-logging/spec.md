# audit-logging Specification

## Purpose
Ownership and shutdown-drain guarantees for asynchronous dashboard audit-log writes, so records are neither lost nor left to leaked tasks.
## Requirements
### Requirement: Asynchronous audit writes remain owned until completion

The system MUST execute `AuditService.log_async()` writes in tracked background tasks without making the calling request wait for database persistence. Each task MUST remain strongly owned until it finishes, and success, cancellation, or failure MUST remove it from the tracked set. An unexpected task failure MUST be consumed and reported rather than becoming an unobserved task exception.

#### Scenario: Audit logging remains fire-and-forget

- **GIVEN** an audit-log database write is blocked
- **WHEN** application code calls `AuditService.log_async()`
- **THEN** the call returns before the database write completes
- **AND** the pending write remains tracked until completion

#### Scenario: Failed audit task is cleaned up

- **WHEN** an asynchronous audit task fails unexpectedly
- **THEN** the failure is reported
- **AND** the completed task is removed from the tracked set

### Requirement: Graceful shutdown drains pending audit writes

Immediately after the in-flight drain attempt returns, graceful shutdown MUST synchronously close asynchronous audit-task admission before any further shutdown await. An `AuditService.log_async()` call after this cutoff MUST remain non-blocking, MUST report the rejected action, and MUST NOT construct a write coroutine or task. Graceful shutdown MUST wait for audit-log tasks accepted before the cutoff for up to `shutdown_drain_timeout_seconds` before closing shared database resources. The drain MUST include tasks that complete or become visible while task-completion callbacks are running. If the deadline expires, the system MUST report each audit task that did not drain before continuing shutdown.

#### Scenario: Late audit producer is rejected after in-flight timeout

- **GIVEN** an HTTP handler remains alive after the in-flight drain timeout
- **AND** graceful shutdown has closed control-plane task admission
- **WHEN** the handler calls `AuditService.log_async()`
- **THEN** the call returns without waiting
- **AND** the rejected action is reported
- **AND** no audit write coroutine or task is created

#### Scenario: Shutdown preserves a pending audit row

- **GIVEN** an asynchronous audit write is still pending when graceful shutdown begins
- **WHEN** the write completes within the configured drain timeout
- **THEN** shutdown waits for the write
- **AND** shared database resources remain open until the write finishes

#### Scenario: Overdue audit write is reported

- **GIVEN** an asynchronous audit write remains pending for the full configured drain timeout
- **WHEN** graceful shutdown drains audit tasks
- **THEN** the drain reports that task as overdue
- **AND** shutdown is allowed to continue

### Requirement: Conversation archive toggles are audited with the actor

Enabling the conversation archive turns the proxy into a full recorder of prompt and response bodies readable by dashboard admins, so the settings API MUST write a dedicated audit event with action `conversation_archive_toggled` whenever a `PUT /api/settings` changes the effective value of `conversation_archive_enabled` — including a clear (`null`) that lets the environment variable or default take over with a different value. The event MUST record the new effective value (`enabled`), the layer it now comes from (`source`), the acting principal (`actor`, which MAY be null when the authentication mode carries no identity, and `actor_role`) and the actor IP, in addition to the ordinary `settings_changed` entry that lists `conversation_archive_enabled` among its changed fields. A `PUT` that stores the value already in effect, or only moves the setting between layers without changing the effective value, MUST NOT emit the dedicated event. The dashboard MUST require an explicit confirmation before it sends `conversation_archive_enabled: true`; the API contract itself is unchanged for other clients.

#### Scenario: Enabling the archive from the dashboard is audited

- **WHEN** an admin confirms enabling the conversation archive and the dashboard sends `PUT /api/settings` with `conversationArchiveEnabled: true`
- **THEN** an audit row with action `conversation_archive_toggled` is written
- **AND** its details record `enabled: true`, `source: "dashboard"`, the actor role and the actor identity when known
- **AND** the `settings_changed` audit row lists `conversation_archive_enabled` in `changed_fields`

#### Scenario: Clearing the dashboard value that turns the archive off is audited

- **GIVEN** the archive is enabled from the dashboard and the environment variable is unset
- **WHEN** an admin clears the dashboard value (`conversationArchiveEnabled: null`)
- **THEN** an audit row with action `conversation_archive_toggled` records `enabled: false` and `source: "default"`

#### Scenario: Re-saving the same value is not a toggle

- **GIVEN** the archive is enabled from the dashboard
- **WHEN** `PUT /api/settings` stores `conversationArchiveEnabled: true` again
- **THEN** no new `conversation_archive_toggled` audit row is written

#### Scenario: The dashboard does not enable without confirmation

- **WHEN** an admin flips the archive switch on in the dashboard
- **THEN** a confirmation dialog states that all prompt/response bodies will be written to each replica's local archive directory
- **AND** no request is sent until the dialog's confirm action is used; cancelling leaves the setting unchanged

