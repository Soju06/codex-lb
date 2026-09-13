# query-caching (delta)

## MODIFIED Requirements

### Requirement: Account request usage summaries combine a persistent rollup with a bounded live tail

Account request-usage summaries MUST NOT aggregate the full `request_logs` history per read. The read MUST combine persisted per-account rollup sums with a live aggregate constrained to rows newer than the rollup watermark, while preserving existing dedupe semantics (latest row id per `(account_id, request_id, requested_at)`) and existing filters (warmup kinds and soft-deleted rows excluded) on the live portion.

The merged summaries MAY be served from a process-local cache keyed by the requested account-id signature for a small fixed TTL, because the displayed lifetime totals tolerate short staleness. Account deletion and duplicate-identity consolidation MUST clear the cache in the process that performed them (they re-attribute or remove usage rather than append to it). A non-positive TTL MUST bypass the cache entirely so tests and precision-sensitive callers observe exact totals.

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

#### Scenario: Cached summaries are served within the TTL per signature

- **GIVEN** a positive summary cache TTL
- **AND** summaries were computed for one account-id signature
- **WHEN** the same signature is requested again within the TTL
- **THEN** the cached summaries MAY be returned without touching the database
- **AND** a different account-id signature MUST NOT be served from that entry

#### Scenario: Account deletion invalidates cached summaries

- **GIVEN** cached summaries that include an account
- **WHEN** that account is deleted, or a duplicate-identity consolidation removes it
- **THEN** the cache MUST be cleared so the next read reflects the new attribution
- **AND** a summary computation already in flight when the invalidation happens MUST NOT re-populate the cache with its pre-invalidation result

### Requirement: A background fold job advances the account usage rollup safely

A periodic background job MUST fold request-log rows into `account_usage_rollups` and advance the watermark. Folding MUST be restricted to rows older than a safety lag, MUST apply the dedupe and filtering semantics of the summary query within the folded window, MUST run on at most one instance at a time, and MUST be idempotent under repeated or concurrent invocation.

#### Scenario: Fold boundary respects the safety lag

- **WHEN** a fold pass runs at time `now`
- **THEN** it MUST NOT fold any row with `requested_at > now − lag`
- **AND** rows younger than the lag remain covered by the live-tail aggregate
- **AND** the lag MUST exceed the maximum possible distance between a row's `requested_at` and the moment its insert becomes visible — `requested_at` is stamped at write time inside the log insert path, so this distance is bounded by replica clock skew, insert-commit latency, and process stalls, not by request duration — because a row landing below the watermark would otherwise vanish from totals
- **AND** post-insert mutations of folded rows MUST NOT rely on the lag: they are fenced by the watermark (skipped below it) or run under the fold-state lock while mirroring the folded sums

#### Scenario: Widening the lag gap is absorbed as ordinary backfill

- **GIVEN** a deployment whose persisted watermark trails `now − lag` by more than one fold cadence (for example after the lag constant is shortened)
- **WHEN** the next fold passes run
- **THEN** the gap MUST be folded in the bounded backfill slices with reported totals unchanged

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
