# query-caching Delta

## ADDED Requirements

### Requirement: Account request usage summaries combine a persistent rollup with a bounded live tail

Account request-usage summaries MUST NOT aggregate the full `request_logs` history per read. The read MUST combine persisted per-account rollup sums with a live aggregate constrained to rows newer than the rollup watermark, while preserving existing dedupe semantics (latest row id per `(account_id, request_id, requested_at)`) and existing filters (warmup kinds and soft-deleted rows excluded) on the live portion.

#### Scenario: Summary read does not scan folded history

- **GIVEN** rollup rows exist with watermark `folded_through = T`
- **WHEN** account request-usage summaries are loaded
- **THEN** the live request-log aggregate MUST constrain to `requested_at > T`
- **AND** the returned totals MUST equal the persisted rollup sums plus the live-tail aggregate per account
- **AND** the cached-input clamp (`cached_input_tokens ≤ input_tokens`) MUST apply to the merged totals

#### Scenario: Summary before the first fold matches legacy behavior

- **GIVEN** no rollup rows exist yet
- **WHEN** account request-usage summaries are loaded
- **THEN** the live aggregate MUST cover all non-deleted, non-warmup request-log history
- **AND** the returned totals MUST equal the pre-rollup query results

#### Scenario: Folding does not change reported totals

- **GIVEN** a set of request-log rows including duplicate rows sharing `(account_id, request_id, requested_at)`
- **WHEN** a fold pass folds part of that history and summaries are read afterwards
- **THEN** the totals MUST equal the totals the legacy full-history dedupe aggregate would report for the same rows

#### Scenario: Summary read is snapshot-consistent with a concurrent fold commit

- **GIVEN** a fold slice may commit at any point during a summary read
- **WHEN** the read fetches rollup sums and the watermark
- **THEN** both MUST come from a single database snapshot (one statement)
- **AND** no qualifying request-log row's contribution may be absent from both the rollup sums and the live-tail aggregate of that read

### Requirement: A background fold job advances the account usage rollup safely

A periodic background job MUST fold request-log rows into `account_usage_rollups` and advance the watermark. Folding MUST be restricted to rows older than a safety lag, MUST apply the dedupe and filtering semantics of the summary query within the folded window, MUST run on at most one instance at a time, and MUST be idempotent under repeated or concurrent invocation.

#### Scenario: Fold boundary respects the safety lag

- **WHEN** a fold pass runs at time `now`
- **THEN** it MUST NOT fold any row with `requested_at > now − lag`
- **AND** rows younger than the lag remain covered by the live-tail aggregate
- **AND** the lag MUST exceed the maximum possible request duration, because a log row is inserted at stream end but dated at request start and a row landing below the watermark would otherwise vanish from totals

#### Scenario: Duplicate rows never split across the fold boundary

- **GIVEN** duplicate request-log rows sharing the same `(account_id, request_id, requested_at)`
- **WHEN** a fold pass selects its window by `requested_at`
- **THEN** all rows of the duplicate group MUST land on the same side of the boundary
- **AND** only the latest row id of the group MUST contribute to the folded sums

#### Scenario: Fold is idempotent and single-writer

- **GIVEN** a fold pass has committed sums through watermark `T`
- **WHEN** another fold pass runs for the same window (repeat invocation or a second instance)
- **THEN** it MUST observe watermark `T` inside its transaction and fold no row at or before `T`
- **AND** no request-log row's contribution appears twice in the rollup

#### Scenario: Historical backfill is sliced and non-blocking

- **GIVEN** a deployment with existing request-log history and no rollup rows
- **WHEN** the first fold passes run
- **THEN** history MUST be folded in bounded time slices, each committed in its own transaction
- **AND** summary reads issued during backfill MUST return correct totals (rollup so far plus remaining live tail)

### Requirement: Account usage rollup rows follow the account lifecycle

Deleting an account MUST delete its rollup row in the same transaction as the account deletion, for both history-preserving and history-deleting variants. Consolidating duplicate accounts into a canonical account MUST transfer the duplicates' rollup sums to the canonical account in the same transaction that reassigns their request logs.

#### Scenario: Account deletion removes its rollup row

- **GIVEN** an account with a rollup row
- **WHEN** the account is deleted (with or without `delete_history`)
- **THEN** the rollup row MUST be deleted in the same transaction
- **AND** subsequent summaries MUST NOT report usage for that account

#### Scenario: Duplicate-account consolidation preserves folded usage

- **GIVEN** a canonical account and a duplicate account that both have folded rollup sums
- **WHEN** identity reconciliation consolidates the duplicate into the canonical account
- **THEN** the duplicate's rollup sums MUST be added to the canonical account's rollup row
- **AND** the duplicate's rollup row MUST be deleted in the same transaction
- **AND** the canonical account's summary MUST equal the combined pre-merge totals
