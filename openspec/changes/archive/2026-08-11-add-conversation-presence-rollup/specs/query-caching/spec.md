## ADDED Requirements

### Requirement: A conversation presence rollup serves distinct-conversation reads

The system SHALL maintain a permanent conversation presence satellite `request_conversation_hourly_rollups` dimensioned by `(bucket_epoch, conversation_id, account_id, is_deleted)` with an additive `request_count` measure, folded from `request_logs` rows whose normalized conversation id (`NULLIF(TRIM(conversation_id), '')`) is non-null and whose `request_kind` is not a warmup kind. `conversation_id` MUST be stored as the normalized value; `is_deleted` MUST be a dimension (not a fold filter) because dashboard conversation reads exclude soft-deleted rows while reports conversation reads include them; `account_id` MUST be carried (NULL-sentinel encoded) solely so account lifecycle mirrors can re-attribute or remove folded presence exactly as the corresponding raw mutation does. The fold SHALL advance a dedicated hour-aligned `conversation_folded_through` watermark on `account_usage_rollup_state` under the shared fold-state row lock, with the established slice contract (DELETE-then-INSERT over half-open hour-aligned windows committed atomically with the watermark advance, bounded paced backfill, fold lag). Rollup rows MUST NOT be deleted by data retention.

#### Scenario: Conversation straddling the fold boundary counts once

- **GIVEN** one conversation with request rows both below and above `conversation_folded_through`
- **WHEN** a switched distinct-conversation read spans both sides
- **THEN** the conversation counts exactly once
- **AND** the additive conversation-request total equals the folded `request_count` sum plus the raw-tail row count

#### Scenario: Soft delete moves presence to the orphaned-deleted dimension

- **GIVEN** folded conversation presence attributed to an account
- **WHEN** the account is soft-deleted (raw history detached with `account_id=NULL, deleted_at=now`)
- **THEN** the folded presence moves to the NULL-sentinel, `is_deleted=true` dimension in the same transaction
- **AND** dashboard conversation reads stop counting it while reports conversation reads keep counting it

#### Scenario: Hard history delete removes only that account's presence

- **GIVEN** a conversation with folded presence from two accounts
- **WHEN** one account is deleted with history removal
- **THEN** only that account's presence rows are removed
- **AND** the conversation still counts through the surviving account's presence, matching a raw scan of the surviving rows

#### Scenario: Fold is idempotent

- **GIVEN** a completed conversation fold pass
- **WHEN** the pass re-runs with the same clock
- **THEN** it commits no slices and the satellite contents are unchanged

### Requirement: Distinct-conversation reads combine the presence rollup with a raw live tail in one statement

The dashboard conversation activity metrics (`conversation_count`, `conversation_request_count`), the dashboard conversation trend buckets, and the UNFILTERED reports summary and per-day conversation counts MUST serve folded history from the presence satellite and the remainder from raw `request_logs`, merged in a single statement per read: the fold watermark joined into both branches of a UNION so the folded segment, its exact raw complement, and the watermark come from one database snapshot, and `COUNT(DISTINCT ...)` deduplicates across the fold boundary. Merged results MUST equal the legacy full-raw aggregation whenever the underlying raw rows still exist. With an epoch or missing watermark the reads MUST degrade to exactly the legacy raw queries (no kill switch). Reports reads carrying account, model, or useragent filters MUST keep the legacy raw statement (the satellite has no such dimensions), and non-hour-multiple dashboard display buckets MUST keep the full-raw path. This reverses the `add-request-log-usage-rollups` non-goal that kept distinct conversation counts raw-bound: conversation statistics over folded history now survive request-log retention pruning, except the documented raw-bound residues (sub-hour window edges, filtered reports reads, and daily-report day-row membership, which stays raw-driven).

#### Scenario: Switched conversation reads equal legacy reads while raw exists

- **GIVEN** a corpus with conversations spanning hours, blank and NULL conversation ids, warmup kinds, and soft-deleted rows
- **WHEN** each switched conversation read runs with the conversation watermark at epoch, mid-history on an hour boundary, and at the fold target — including states where the hourly and conversation watermarks differ
- **THEN** every result equals the legacy raw-only implementation exactly

#### Scenario: Conversation statistics survive raw pruning

- **GIVEN** folded conversation presence whose source raw rows have been pruned by retention
- **WHEN** the dashboard conversation activity metrics, hour-multiple conversation trend buckets, or the unfiltered reports summary conversation count are read over that period
- **THEN** the distinct-conversation values equal those reported before the pruning (modulo the documented sub-bucket window edges)

#### Scenario: Filtered reports reads stay raw-bound

- **GIVEN** a reports summary or daily read filtered by account, model, or useragent group
- **WHEN** the read executes
- **THEN** it uses the legacy raw statement and reaches only as far back as raw retention keeps rows

#### Scenario: Non-hour-multiple conversation buckets degrade to full raw

- **GIVEN** a conversation trend request with a display bucket that is not a whole multiple of the rollup hour
- **WHEN** the aggregate is calculated
- **THEN** the legacy full-raw query is used unchanged

## MODIFIED Requirements

### Requirement: Dashboard conversation trends aggregate by bucket

The dashboard conversation trend query MUST group by the configured time bucket
and count distinct non-empty normalized conversation IDs within each bucket. It
MUST exclude warmup traffic and MUST NOT use model or service-tier grouping that
could cause one conversation to be counted more than once in a bucket. For
hour-multiple display buckets the count MUST merge the conversation presence
rollup with the raw live tail through a UNION before the distinct count, so a
conversation appearing in both the folded segment and the raw tail of one
display bucket still counts once.

#### Scenario: One conversation across model groups counts once per bucket

- **GIVEN** a bucket contains two non-warmup request logs for `conv-a` under
  different models and one log for `conv-b`
- **WHEN** the dashboard conversation trend aggregate is calculated
- **THEN** that bucket's conversation count is `2`

#### Scenario: One conversation across the fold boundary counts once per bucket

- **GIVEN** a display bucket containing rows for `conv-a` below the
  conversation watermark (rollup-served) and above it (raw-served)
- **WHEN** the dashboard conversation trend aggregate is calculated
- **THEN** that bucket's conversation count counts `conv-a` once
