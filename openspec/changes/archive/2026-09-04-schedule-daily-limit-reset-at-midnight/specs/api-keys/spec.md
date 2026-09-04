## MODIFIED Requirements

### Requirement: Weekly token usage reset

The system SHALL keep the existing lazy on-read reset strategy for API key usage limits. When validating an API key, if a limit `reset_at < now()`, the system MUST reset the counter and advance `reset_at` by whole window intervals until it is in the future. The system MUST also run an hourly background fallback sweep that repairs expired API key limit usage even when no validation request arrives.

A newly calculated `daily` limit reset timestamp MUST be the next 00:00 UTC boundary rather than 24 hours after the calculation instant. At 23:50 UTC each day, the singleton API-key limit scheduler MUST run a leader-gated alignment pass that changes every non-aligned `daily` limit's `reset_at` to the next 00:00 UTC boundary without changing `current_value`. The alignment pass MUST NOT change reset timestamps for any other limit window.

#### Scenario: Weekly reset triggered on validation

- **WHEN** an API key is validated and `weekly_reset_at` is 2 weeks in the past
- **THEN** `weekly_tokens_used` is set to 0 and `weekly_reset_at` is advanced by 14 days (2 × 7 days) to a future date

#### Scenario: No reset needed

- **WHEN** an API key is validated and `weekly_reset_at` is in the future
- **THEN** no reset occurs; `weekly_tokens_used` retains its current value

#### Scenario: Hourly fallback resets expired usage without a read

- **WHEN** an API key usage limit is expired and no validation request occurs
- **THEN** the hourly background fallback resets `current_value` to 0 and advances `reset_at` into the future

#### Scenario: New daily limit uses the next UTC midnight

- **WHEN** the system creates or explicitly resets a `daily` API-key limit at any time before 00:00 UTC
- **THEN** the limit's `reset_at` is the immediately following 00:00 UTC boundary

#### Scenario: Daily alignment runs before midnight

- **GIVEN** one or more `daily` API-key limits have reset timestamps that are not the next 00:00 UTC boundary
- **WHEN** the leader-gated daily alignment pass runs at 23:50 UTC
- **THEN** each affected limit's `reset_at` becomes the next 00:00 UTC boundary
- **AND** each affected limit retains its existing `current_value`

#### Scenario: Daily alignment leaves other windows unchanged

- **GIVEN** API-key limits exist for `5h`, `7d`, `weekly`, or `monthly` windows
- **WHEN** the daily alignment pass runs at 23:50 UTC
- **THEN** their reset timestamps and current usage values remain unchanged

#### Scenario: Replicas do not duplicate the alignment pass

- **GIVEN** multiple replicas share the API-key limit table
- **WHEN** their daily alignment timers reach 23:50 UTC
- **THEN** only the replica holding the shared scheduler leader lease executes the alignment pass
