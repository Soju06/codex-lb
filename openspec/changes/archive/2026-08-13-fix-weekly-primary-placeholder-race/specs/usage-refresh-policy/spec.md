## ADDED Requirements

### Requirement: Weekly-primary remap tiebreak is data-aware within a fetch

The weekly-primary to secondary remap tiebreak (`should_use_weekly_primary` / `normalize_weekly_only_rows`) MUST be data-aware within a single refresh fetch and MUST NOT let a sub-second `recorded_at` difference between same-fetch rows decide the winner.

A row carries real quota metadata when it has a positive `window_minutes` AND a non-null `reset_at`; a row that lacks both is a no-data placeholder. For the data-aware tiebreak, a no-data placeholder MUST be classified as the absence of a measurement and MUST NOT be treated as a measurement of zero usage merely because its stored `used_percent` is zero — a timestamped placeholder must not beat an untimestamped real row, and a same-fetch real row must not be displaced by a placeholder. When two competing rows are from the same fetch (their `recorded_at` values differ by at most `SIBLING_FETCH_MARGIN_SECONDS`, 5.0 seconds, or one/both timestamps are unavailable), a weekly `primary` row that carries real quota metadata MUST be selected over a competing `secondary` row that is a no-data placeholder, and a real `secondary` row MUST be selected over a no-data `primary` placeholder. (Rendering a newer no-data placeholder that wins a cross-fetch comparison as an explicit "unavailable" window is out of scope for this change; the cross-fetch winner is rendered per existing placeholder rules.)

When both rows carry `recorded_at` and their difference is strictly greater than `SIBLING_FETCH_MARGIN_SECONDS`, the rows are from genuinely different fetches and the newer row MUST win — a later fetch is more authoritative about what upstream currently reports. This preserves the pre-fix cross-fetch behavior so a stale real weekly primary cannot freeze the weekly value over a fresh placeholder from a later fetch.

This tiebreak MUST be shared by every consumer of `should_use_weekly_primary`, including account-summary remap, dashboard overview and projection aggregation, and per-bucket account usage trend remap, so the weekly quota is reported consistently across all surfaces.

#### Scenario: Same-fetch real weekly primary beats a no-data secondary placeholder

- **GIVEN** an account whose latest `primary` usage row reports a weekly window (`window_minutes == 10080`) with a non-null `reset_at` and `used_percent` below 100
- **AND** the latest `secondary` usage row is a no-data placeholder (`window_minutes` falsy or null, `reset_at` null, `used_percent` 0.0, no credit metadata)
- **AND** the two rows were recorded within `SIBLING_FETCH_MARGIN_SECONDS` (5.0 seconds) of each other in the same refresh cycle
- **WHEN** the system derives the effective secondary (weekly) usage window for account summaries, dashboard overview/projection aggregation, or account usage trends
- **THEN** the weekly `primary` row is selected as the source of weekly usage
- **AND** the reported weekly remaining percent equals `100 - primary.used_percent`
- **AND** the reported value does not jump to 100% remaining

#### Scenario: Real secondary beats a no-data primary placeholder in the same fetch

- **GIVEN** an account whose latest `secondary` usage row carries real quota metadata (positive `window_minutes` and a non-null `reset_at`)
- **AND** the latest `primary` usage row is a no-data placeholder
- **AND** the two rows were recorded within `SIBLING_FETCH_MARGIN_SECONDS` of each other
- **WHEN** the system derives the effective secondary usage window
- **THEN** the real `secondary` row is selected as the source of weekly usage
- **AND** the reported weekly remaining percent reflects that row's `used_percent`

#### Scenario: Genuinely newer row from a later fetch wins regardless of metadata

- **GIVEN** an account whose latest `primary` usage row reports a weekly window with real quota metadata but was written in an earlier fetch
- **AND** a later fetch wrote a competing `secondary` row whose `recorded_at` is more than `SIBLING_FETCH_MARGIN_SECONDS` (5.0 seconds) after the primary row
- **WHEN** the system derives the effective secondary usage window
- **THEN** the newer row from the later fetch is selected
- **AND** the stale real weekly primary does not freeze the weekly value indefinitely

#### Scenario: Two real same-fetch weekly rows resolve by reset-at precedence

- **GIVEN** an account whose latest `primary` and `secondary` usage rows both carry real quota metadata
- **AND** the two rows were recorded within `SIBLING_FETCH_MARGIN_SECONDS` (5.0 seconds) of each other in the same refresh cycle
- **WHEN** the system derives the effective secondary usage window across repeated refresh cycles
- **THEN** the selected row is determined by reset-at precedence and the stable weekly-primary default
- **AND** the selection does not flip between the two rows on a sub-second `recorded_at` difference
