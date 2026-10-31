## ADDED Requirements

### Requirement: Published-rate API key credit budgets

A configured `credits` rule MUST accrue a local per-key budget from the versioned published Codex text-token rate card. It MUST NOT represent exact included-subscription quota or a debit of the provider's purchased-credit balance. USD API prices and aggregate account-capacity estimates MUST NOT supply credit conversion rates.

#### Scenario: Fractional traffic exhausts a one-credit rule
- **GIVEN** a one-credit rule and Standard `gpt-5.6-sol` requests
- **WHEN** two requests each settle 6,000 uncached input tokens
- **THEN** the stored counter is 1.2 credits rather than zero
- **AND** the next request is refused

### Requirement: Durable fractional credit settlement

Admission MUST reserve request-aware credit estimates. Settlement MUST price actual input, cached input and output tokens using the reservation's admitted model and requested credit tier. It MUST retain fractions in integer units of 10^-8 credits, ignore response-only pricing labels, and preserve exactly-once settlement, release and reset fences.

#### Scenario: Pricing survives sessions and response labels
- **GIVEN** a persisted Fast `gpt-6.1-sol` reservation
- **WHEN** another session settles 100,000 cached input tokens with different response model and tier labels
- **THEN** the admitted pricing basis charges 0.5 credits exactly once

#### Scenario: Failures without usage do not charge
- **WHEN** an admitted request fails with no reported token usage
- **THEN** settlement charges zero credits

### Requirement: Credit admission refuses unsupported prices

Credit admission MUST use Standard rates for absent, `auto` and `default` tiers, 2 for `fast`/`priority`, and 6 for supported `ultrafast` models. It MUST refuse unpriced models and tiers, including `flex`, before upstream dispatch even at zero estimated usage. Model-less metadata MUST reserve zero credits but still respect exhausted global limits.

#### Scenario: Unpriced admission is not free
- **WHEN** an unpriced request has an applicable credit rule
- **THEN** admission refuses it even at zero estimated token usage
- **AND** the transaction leaves no committed partial reservation

#### Scenario: Metadata preserves zero-charge admission
- **WHEN** a model-less metadata request is below its global credit limit
- **THEN** admission reserves zero credits

### Requirement: Whole-credit API presentation

Public maximum, used and remaining fields MUST retain integer whole-credit units. Used credits MUST be rounded down only for presentation. Remaining credit MUST be derived from the presented maximum and used values. Enforcement MUST use the unrounded stored counter.

#### Scenario: Fractional usage retains integer API fields
- **WHEN** a one-credit rule has consumed 0.5 credits
- **THEN** its public maximum, used and remaining fields are 1, 0 and 1
- **AND** its stored counter retains 0.5 credits

#### Scenario: Exhausted usage reports no remaining credit
- **WHEN** a one-credit rule has consumed 1.2 credits
- **THEN** its public used and remaining fields are 1 and 0

### Requirement: Credit history reconstruction

A new credit rule without `resetUsage` MUST reconstruct that key's current-window non-warmup logs by model and requested tier; missing requested tier means Standard. It MUST include failed requests with reported tokens and reject unpriced token-bearing history. Editing a matching rule MUST preserve its fractional counter and reset time.

#### Scenario: History and matching-rule edits preserve fractions
- **WHEN** a credit rule is added after cached, uncached and failed token-bearing requests
- **THEN** reconstruction prices only that key's current-window non-warmup usage
- **AND** a later maximum-only edit preserves its fractional counter

### Requirement: Credit counter migration

The migration MUST scale existing credit limits and reservation-item counters, preserve other types, and add a nullable reservation credit tier. It MUST check every conversion range before mutation. Every writer MUST be paused for upgrade or downgrade; mixed old and new units are unsupported. Downgrade MUST disclose that subsequently accumulated fractions are discarded.

#### Scenario: Migration preserves safe legacy values
- **WHEN** safe whole-credit counters are upgraded and downgraded without new usage
- **THEN** the original counters and nullable ledger values are restored
- **AND** non-credit limits are unchanged

#### Scenario: Unsafe counters stop before mutation
- **WHEN** any converted table contains an unsafe legacy counter
- **THEN** upgrade stops before conversion or schema mutation

### Requirement: Bounded credit counter storage

Credit maxima MUST fit signed 64-bit fixed-point storage. Credit counters MUST saturate at the physical integer ceiling and remain exhausted until reset. A later reservation release MUST NOT reopen a saturated credit counter.

#### Scenario: Late releases cannot reopen a saturated budget
- **WHEN** settlement reaches the physical credit-storage ceiling with another reservation still pending
- **AND** that other reservation is subsequently released
- **THEN** the counter remains the signed 64-bit integer maximum
- **AND** further admission is refused until reset

## MODIFIED Requirements

### Requirement: API Key update
The system SHALL allow updating key properties via `PATCH /api/api-keys/{id}`. Updatable fields: `name`, `allowedModels`, `weeklyTokenLimit`, `expiresAt`, `isActive`, `usageSections`, `transportPolicyOverride`. The key hash and prefix MUST NOT be modifiable. The system MUST accept timezone-aware ISO 8601 datetimes for `expiresAt` and normalize them to UTC naive before persistence. The `transportPolicyOverride` field MUST accept `null` (follow the global policy) or one of `"smart"`, `"always_http"`, `"always_websocket"`; any other value MUST be rejected with HTTP 400.

When a submitted API key limit rule matches an existing rule by `limit_type`, `limit_window`, and `model_filter`, updating the rule's maximum MUST preserve the latest committed `current_value` and `reset_at` unless `resetUsage` is true. A transient SQLite lock or snapshot conflict during the update MUST roll back and retry the complete read/build/write transaction, including rereading existing limits, before returning an error.

When a submitted API key limit rule does not match an existing rule by `limit_type`, `limit_window`, and `model_filter`, the system MUST initialize the new rule's `current_value` from the API key's existing request-log usage in that rule's current window. Token and cost rules MUST include only successful requests. Credit rules MUST also include failed requests with reported token usage, matching credit settlement. If `resetUsage` is true, the system MUST initialize submitted limits with `current_value: 0`.

#### Scenario: Update key with timezone-aware expiration
- **WHEN** admin submits `PATCH /api/api-keys/{id}` with `{ "expiresAt": "2025-12-31T00:00:00Z" }`
- **THEN** the system persists the expiration successfully without PostgreSQL datetime binding errors
- **AND** the response returns `expiresAt` representing the same UTC instant

#### Scenario: Update non-existent key

- **WHEN** admin submits `PATCH /api/api-keys/{id}` with an unknown ID
- **THEN** the system returns 404

#### Scenario: Add token limit after current-window usage exists

- **WHEN** an API key has successful request-log token usage in the active daily window
- **AND** the API key has error or incomplete request-log token usage in the same window
- **AND** admin submits `PATCH /api/api-keys/{id}` adding a daily `total_tokens` limit without `resetUsage`
- **THEN** the new limit's `current_value` includes only the successful current-window token usage

#### Scenario: Add cost limit after current-window usage exists

- **WHEN** an API key has successful request-log costs in the active daily window
- **AND** admin submits `PATCH /api/api-keys/{id}` adding a daily `cost_usd` limit without `resetUsage`
- **THEN** the new limit's `current_value` is the sum of each successful request log's `cost_usd` converted to truncated integer microdollars

#### Scenario: Reset usage when adding a limit

- **WHEN** an API key has request-log usage in the active window
- **AND** admin submits `PATCH /api/api-keys/{id}` adding a limit with `resetUsage: true`
- **THEN** the new limit's `current_value` is `0`

#### Scenario: Update key transport policy override

- **WHEN** admin submits `PATCH /api/api-keys/{id}` with `{ "transportPolicyOverride": "always_http" }`
- **THEN** the system persists the override and returns `transportPolicyOverride = "always_http"`

#### Scenario: Clear key transport policy override

- **WHEN** admin submits `PATCH /api/api-keys/{id}` with `{ "transportPolicyOverride": null }`
- **THEN** the system clears the override and the key follows the global `http_downstream_transport_policy`

#### Scenario: Reject invalid transport policy override

- **WHEN** admin submits `PATCH /api/api-keys/{id}` with `{ "transportPolicyOverride": "carrier-pigeon" }`
- **THEN** the system returns 400 and does not modify the key

#### Scenario: Preserve matched limit usage during PATCH

- **GIVEN** an API key has a matched limit with committed `current_value` and `reset_at`
- **WHEN** admin submits a PATCH that changes the matched limit's maximum without `resetUsage`
- **THEN** the limit maximum is updated
- **AND** the latest committed `current_value` and `reset_at` remain unchanged

#### Scenario: Retry API-key PATCH after a transient SQLite snapshot conflict

- **GIVEN** a concurrent reservation or lazy reset commits after the PATCH's initial read
- **WHEN** the PATCH write encounters a transient SQLite lock or snapshot conflict
- **THEN** the transaction is rolled back
- **AND** the PATCH rereads current state and retries the complete update
- **AND** the PATCH succeeds without clearing the concurrent usage state

### Requirement: Limit update with usage state preservation
When updating API key limits, the system SHALL preserve existing usage state (`current_value`, `reset_at`) for unchanged limit rules. Limit comparison key is `(limit_type, limit_window, model_filter)`.

- Matching existing rule: `current_value` and `reset_at` SHALL be preserved; only `max_value` is updated
- New rule (no match) without `resetUsage`: `current_value` SHALL be initialized from the API key's existing request-log usage in the new rule's current window, with a fresh `reset_at`. Token and cost rules SHALL count successful requests only; credit rules SHALL also count failed requests with reported token usage
- New rule (no match) with `resetUsage`: `current_value=0` and fresh `reset_at`
- Removed rule (in existing but not in update): row is deleted

Usage reset SHALL only occur via an explicit action (`resetUsage` field or dedicated endpoint), never as a side-effect of metadata or policy edits.

#### Scenario: Metadata-only edit preserves usage state

- **WHEN** an API key PATCH updates only name or is_active
- **AND** `limits` field is not included in the payload
- **THEN** existing `current_value` and `reset_at` are unchanged

#### Scenario: Same policy re-submission preserves usage state

- **WHEN** an API key PATCH includes `limits` with identical rules (same type/window/filter/max_value)
- **THEN** existing `current_value` and `reset_at` are unchanged

#### Scenario: max_value adjustment preserves counters

- **WHEN** an API key PATCH changes only `max_value` for an existing matched limit rule
- **THEN** that rule's existing `current_value` and `reset_at` are unchanged

#### Scenario: Adding a new limit backfills current-window usage

- **WHEN** an API key has successful and failed token-bearing request-log usage in the active window
- **AND** an API key PATCH adds a limit rule that does not match any existing rule
- **AND** `resetUsage` is not true
- **THEN** the new rule's `current_value` reflects that key's usage in the rule's current window
- **AND** token and cost rules include successful requests only
- **AND** credit rules also include failed requests with reported token usage
- **AND** the new rule receives a fresh `reset_at`

#### Scenario: resetUsage keeps new limits at zero

- **WHEN** an API key has request-log usage in the active window
- **AND** an API key PATCH adds a limit rule that does not match any existing rule
- **AND** `resetUsage` is true
- **THEN** the new rule's `current_value` is `0`
- **AND** the new rule receives a fresh `reset_at`
