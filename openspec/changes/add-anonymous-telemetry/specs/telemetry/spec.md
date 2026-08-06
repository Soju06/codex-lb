# Add anonymous telemetry

## ADDED Requirements

### Requirement: Telemetry payload field allowlist

The service MUST transmit only fields defined in the telemetry payload schema in this capability's `context.md`, and MUST NOT transmit account emails, workspace identifiers, client IP addresses, API keys, request or response content, raw user-agent strings, per-account records, or free-text error messages in any telemetry payload.

The payload schema is versioned (`schema_version`). Adding a field requires a spec change to
this capability; the payload builder test suite MUST fail when the built payload contains a
field not present in the documented schema.

#### Scenario: Snapshot contains only allowlisted fields

- **WHEN** the telemetry snapshot builder produces a payload
- **THEN** every top-level and nested field is present in the documented schema, and a
  schema-snapshot regression test rejects any undeclared field

#### Scenario: Identifying data never serialized

- **WHEN** the snapshot is built on an instance with linked accounts, API keys, and request
  logs containing raw user agents and error messages
- **THEN** the serialized payload contains no email, workspace ID, IP address, API key
  material, raw user-agent string, or free-text error message

### Requirement: Consent state and default activation

Telemetry consent MUST be a persisted tri-state (`undecided`, `enabled`, `disabled`) defaulting to `undecided`, and while consent is `undecided` the service SHALL treat telemetry as active.

Upgrading an existing installation MUST introduce the consent state as `undecided` (existing
users get the same informed default-on treatment as new installs).

#### Scenario: Fresh install defaults to active

- **WHEN** codex-lb starts for the first time with no persisted consent and no environment
  override
- **THEN** consent is `undecided` and telemetry snapshots are transmitted

#### Scenario: Upgrade treats existing users as undecided

- **WHEN** an existing installation migrates to a version with this capability
- **THEN** the migrated consent state is `undecided` and the one-time consent dialog is shown
  on next dashboard entry

### Requirement: One-time consent dialog with exact payload preview

The dashboard MUST present a one-time consent dialog on first entry while consent is `undecided`, and the dialog MUST display the exact JSON payload the instance would transmit at that moment.

A decision (enable or disable) MUST be persisted and the dialog MUST NOT be shown again after
any decision. The dialog MUST offer disabling with no fewer clicks than enabling.

#### Scenario: Undecided operator sees payload preview

- **WHEN** an operator opens the dashboard while consent is `undecided`
- **THEN** a dialog shows the live snapshot JSON with equally prominent enable and disable
  actions

#### Scenario: Decision is final until changed in settings

- **WHEN** the operator chooses disable in the dialog
- **THEN** consent persists as `disabled`, no snapshot is transmitted afterward, and the
  dialog never reappears

### Requirement: Settings toggle and environment kill switch

The dashboard settings MUST expose a telemetry toggle reflecting the resolved consent state, and the environment variable `CODEX_LB_TELEMETRY_ENABLED` MUST override persisted consent when set (`false` disables all transmission, `true` enables and suppresses the consent dialog).

#### Scenario: Headless deployment disables via environment

- **WHEN** the service runs with `CODEX_LB_TELEMETRY_ENABLED=false`
- **THEN** no telemetry network traffic occurs regardless of persisted consent, and the
  settings toggle shows telemetry as disabled by environment override

#### Scenario: Toggle flips persisted consent

- **WHEN** the operator disables telemetry in settings without an environment override
- **THEN** consent persists as `disabled` and transmission stops without restart

### Requirement: Startup notice while undecided

While consent is `undecided`, the service MUST emit a single startup log line stating that anonymous telemetry is active, where the collected-field documentation lives, and how to disable it.

#### Scenario: Headless operator is informed

- **WHEN** the service starts with consent `undecided`
- **THEN** exactly one log line names the telemetry documentation location and the
  `CODEX_LB_TELEMETRY_ENABLED=false` disable path

### Requirement: Disabled means zero telemetry traffic

When resolved consent is `disabled`, the service MUST NOT open any network connection to the telemetry endpoint.

#### Scenario: No connection attempts when disabled

- **WHEN** telemetry is disabled and the service runs through startup and a 24-hour scheduler
  cycle
- **THEN** no connection attempt to the telemetry endpoint is made

### Requirement: Client family allowlist mapping

Telemetry client statistics MUST report only canonical client-family identifiers produced by the documented mapping table, MUST map any unmatched user-agent group to `other`, and MUST NOT transmit raw user-agent group values.

The canonical mapping table (raw `useragent_group` → family):

| Raw group(s) | Family |
| --- | --- |
| `codex_exec`, `codex-tui` | `codex-cli` |
| `Codex Desktop` | `codex-desktop` |
| `codex_vscode` | `codex-vscode` |
| `AsyncOpenAI` | `openai-sdk-python` |
| `OpenAI` | `openai-sdk-js` |
| `ai`, `ai-sdk` | `vercel-ai-sdk` |
| `opencode` | `opencode` |
| `Mozilla` | `browser` |
| `curl`, `undici`, `node`, `Python-urllib`, `python-requests`, `aiohttp` | `script` |
| anything else | `other` |

The payload MUST include `clients_other_ratio` so mapping coverage decay is observable
without ever transmitting the unmatched raw values.

#### Scenario: Private tool names never leave the instance

- **WHEN** request logs contain a user-agent group not present in the mapping table
- **THEN** its traffic is attributed to `other` and the raw group string is absent from the
  payload

#### Scenario: Codex CLI variants collapse to one family

- **WHEN** traffic exists from both `codex_exec` and `codex-tui`
- **THEN** the payload reports a single `codex-cli` family combining both

### Requirement: Model catalog allowlist with per-model reasoning mix

Telemetry model statistics MUST include only model names present in the official model catalog allowlist, MUST map unmatched model names to `other`, and MUST report reasoning-effort distribution nested per model entry rather than as an instance-global aggregate.

#### Scenario: Custom model source names are not transmitted

- **WHEN** an operator has configured a custom model source with a private model name
- **THEN** that traffic appears under `other` and the private name is absent from the payload

#### Scenario: Reasoning effort is model-scoped

- **WHEN** the snapshot reports models
- **THEN** each model entry carries its own reasoning-effort share map and no global
  reasoning mix field exists

### Requirement: Random instance identity

The telemetry instance identifier MUST be a UUID generated randomly on first run, MUST NOT be derived from hardware, network, account, or operating-system identity, and MUST be regenerated if deleted.

#### Scenario: Identifier carries no fingerprint

- **WHEN** the instance identifier is created
- **THEN** it is a random UUIDv4 persisted locally, and deleting it yields a fresh unrelated
  identifier on next start

### Requirement: Transmission cadence and failure isolation

The service SHALL transmit one snapshot at startup and one per 24-hour interval thereafter, and telemetry transmission failures MUST NOT affect proxy operation, MUST use a bounded timeout, MUST NOT retry more than once per interval, and MUST log failures at debug level only.

#### Scenario: Collection endpoint outage is invisible

- **WHEN** the telemetry endpoint is unreachable
- **THEN** proxy requests are unaffected, startup is not delayed beyond the bounded timeout,
  and no warning-or-higher log noise is produced

### Requirement: Bucketed sensitive aggregates

Account pool size, per-plan account counts, API key count, database size, and cost aggregates MUST be transmitted as documented buckets, never as exact values.

#### Scenario: Pool size is a bucket

- **WHEN** an instance has 13 linked accounts
- **THEN** the payload reports the `6-20` bucket and no exact account count
