## ADDED Requirements

### Requirement: Weekly-primary remap tiebreak is data-aware

The weekly-primary to secondary remap tiebreak (`should_use_weekly_primary` / `normalize_weekly_only_rows`) MUST be data-aware and MUST NOT be decided by a sub-second `recorded_at` difference between rows written in the same refresh cycle.

A row carries real quota metadata when it has a positive `window_minutes` AND a non-null `reset_at`; a row that lacks both is a no-data placeholder, not a measurement of zero usage. A weekly `primary` row that carries real quota metadata MUST be selected over a competing `secondary` row that is a no-data placeholder, and a real `secondary` row MUST be selected over a no-data `primary` placeholder, regardless of `recorded_at` ordering. A no-data placeholder MUST NOT be treated as a measurement of zero usage.

Two rows whose `recorded_at` values differ by less than the sibling-fetch margin (`_SIBLING_FETCH_MARGIN_SECONDS`, 5.0 seconds) MUST be treated as same-fetch and resolved by reset-at precedence (and the stable weekly-primary default) rather than by the sub-second `recorded_at` difference. The `recorded_at` comparison MAY still decide the winner only between two rows that both carry real quota metadata AND were written in genuinely different fetches (differing by more than the sibling-fetch margin).

This tiebreak MUST be shared by every consumer of `should_use_weekly_primary`, including account-summary remap, dashboard overview and projection aggregation, and per-bucket account usage trend remap, so the weekly quota is reported consistently across all surfaces.

#### Scenario: Real weekly primary beats a no-data secondary placeholder

- **GIVEN** an account whose latest `primary` usage row reports a weekly window (`window_minutes == 10080`) with a non-null `reset_at` and `used_percent` below 100
- **AND** the latest `secondary` usage row is a no-data placeholder (`window_minutes` falsy or null, `reset_at` null, `used_percent` 0.0, no credit metadata)
- **AND** the secondary placeholder was recorded within milliseconds after the primary row in the same refresh cycle
- **WHEN** the system derives the effective secondary (weekly) usage window for account summaries, dashboard overview/projection aggregation, or account usage trends
- **THEN** the weekly `primary` row is selected as the source of weekly usage
- **AND** the reported weekly remaining percent equals `100 - primary.used_percent`
- **AND** the reported value does not jump to 100% remaining

#### Scenario: Real secondary beats a no-data primary placeholder

- **GIVEN** an account whose latest `secondary` usage row carries real quota metadata (positive `window_minutes` and a non-null `reset_at`)
- **AND** the latest `primary` usage row is a no-data placeholder
- **AND** the primary placeholder was recorded within milliseconds after the secondary row in the same refresh cycle
- **WHEN** the system derives the effective secondary usage window
- **THEN** the real `secondary` row is selected as the source of weekly usage
- **AND** the reported weekly remaining percent reflects that row's `used_percent`

#### Scenario: Genuinely newer real secondary supersedes a stale weekly primary

- **GIVEN** an account whose latest `primary` usage row reports a weekly window with real quota metadata but was written in an earlier fetch
- **AND** a later fetch wrote a real `secondary` usage row whose `recorded_at` is more than the sibling-fetch margin (5.0 seconds) after the primary row
- **WHEN** the system derives the effective secondary usage window
- **THEN** the newer real `secondary` row is selected
- **AND** weekly usage is not frozen on the stale primary sample

#### Scenario: Two real same-fetch weekly rows do not flip on sub-second timing

- **GIVEN** an account whose latest `primary` and `secondary` usage rows both carry real quota metadata
- **AND** the two rows were written within the sibling-fetch margin (5.0 seconds) of each other in the same refresh cycle
- **WHEN** the system derives the effective secondary usage window across repeated refresh cycles
- **THEN** the selected row is determined by reset-at precedence and the stable weekly-primary default
- **AND** the selection does not flip between the two rows on a sub-second `recorded_at` difference
