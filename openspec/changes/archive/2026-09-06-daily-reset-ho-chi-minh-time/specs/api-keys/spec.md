## MODIFIED Requirements

### Requirement: Weekly token usage reset

The system SHALL keep the existing lazy on-read reset strategy for API key usage limits. When validating an API key, if a limit `reset_at < now()`, the system MUST reset the counter and advance `reset_at` into the future. Non-daily limits MUST advance by whole window intervals. Expired daily limits MUST advance to the next 00:00 Asia/Ho_Chi_Minh (UTC+7) boundary. The system MUST also run an hourly background fallback sweep that repairs expired API key limit usage even when no validation request arrives.

A newly calculated `daily` limit reset timestamp MUST be the next 00:00 Asia/Ho_Chi_Minh (UTC+7) boundary, corresponding to 17:00 UTC. At 23:50 Asia/Ho_Chi_Minh (16:50 UTC) each day, the singleton API-key limit scheduler MUST run a leader-gated alignment pass that changes every non-aligned `daily` limit's `reset_at` to the next local midnight without changing `current_value`. The alignment pass MUST NOT change reset timestamps for any other limit window. Stored reset timestamps MUST remain UTC and the daily schedule MUST be independent of the host timezone.

#### Scenario: Weekly reset triggered on validation

- **WHEN** an API key is validated and `weekly_reset_at` is 2 weeks in the past
- **THEN** `weekly_tokens_used` is set to 0 and `weekly_reset_at` is advanced by 14 days (2 × 7 days) to a future date

#### Scenario: No reset needed

- **WHEN** an API key is validated and `weekly_reset_at` is in the future
- **THEN** no reset occurs; `weekly_tokens_used` retains its current value

#### Scenario: Hourly fallback resets expired usage without a read

- **WHEN** an API key usage limit is expired and no validation request occurs
- **THEN** the hourly background fallback resets `current_value` to 0 and advances `reset_at` into the future

#### Scenario: New daily limit uses the next Ho Chi Minh City midnight

- **WHEN** the system creates or explicitly resets a `daily` API-key limit at 2026-09-06 16:59 UTC (23:59 Asia/Ho_Chi_Minh)
- **THEN** the limit's `reset_at` is 2026-09-06 17:00 UTC (2026-09-07 00:00 Asia/Ho_Chi_Minh)

#### Scenario: Daily limit calculated at local midnight uses the following day

- **WHEN** a daily reset timestamp is calculated at 2026-09-06 17:00 UTC or later that UTC evening
- **THEN** the next reset timestamp is 2026-09-07 17:00 UTC

#### Scenario: Expired daily limit converges to local midnight

- **GIVEN** a daily limit still has a legacy reset timestamp at 00:00 UTC
- **WHEN** validation or the hourly fallback resets it after expiry
- **THEN** its usage counter is zeroed and its next reset is the next 17:00 UTC boundary

#### Scenario: Daily alignment runs before midnight

- **GIVEN** one or more `daily` API-key limits have reset timestamps that are not the next 00:00 Asia/Ho_Chi_Minh boundary
- **WHEN** the leader-gated daily alignment pass runs at 23:50 Asia/Ho_Chi_Minh (16:50 UTC)
- **THEN** each affected limit's `reset_at` becomes the next 00:00 Asia/Ho_Chi_Minh boundary (17:00 UTC)
- **AND** each affected limit retains its existing `current_value`

#### Scenario: Daily alignment leaves other windows unchanged

- **GIVEN** API-key limits exist for `5h`, `7d`, `weekly`, or `monthly` windows
- **WHEN** the daily alignment pass runs at 23:50 Asia/Ho_Chi_Minh
- **THEN** their reset timestamps and current usage values remain unchanged

#### Scenario: Replicas do not duplicate the alignment pass

- **GIVEN** multiple replicas share the API-key limit table
- **WHEN** their daily alignment timers reach 23:50 Asia/Ho_Chi_Minh
- **THEN** only the replica holding the shared scheduler leader lease executes the alignment pass
