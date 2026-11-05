## ADDED Requirements

### Requirement: Dashboard request-log filtering supports upstreams

The dashboard request logs view SHALL allow operators to filter rows by one or more upstreams using stable upstream identifiers: `subscription_pool` for rows with no model source, or the exact `model_source_id` of a model source. `GET /api/request-logs` MUST accept repeated `upstream` parameters, combine the selected upstreams with OR semantics, and combine the upstream facet with the account, API-key, model, status, search, and timeframe filters with AND semantics. Requests without `upstream` MUST keep their existing results. `GET /api/request-logs/options` MUST return an `upstreams` list containing only upstreams observed in matching, non-soft-deleted request logs, each with `id`, `type` (`subscription_pool` or `model_source`), `displayName`, `modelSourceKind`, and `isDeleted`. The upstream options MUST ignore the request's own `upstream` selection while every other facet applies it. Labels MUST come from the current `model_sources` name and kind when the source exists; a source that no longer exists MUST keep its option with `isDeleted` set and the kind persisted on its request logs. The response MUST NOT expose model-source base URLs, credentials, or API-key material.

#### Scenario: Filter by one model source
- **GIVEN** request logs routed through the subscription pool and through model source `src_a`
- **WHEN** a client requests `GET /api/request-logs?upstream=src_a`
- **THEN** only rows with `model_source_id = src_a` are returned
- **AND** `total` counts only those rows

#### Scenario: Mixed upstream selection uses OR semantics
- **WHEN** a client requests `upstream=subscription_pool&upstream=src_a`
- **THEN** rows with no model source and rows from `src_a` are returned
- **AND** rows from any other model source are not

#### Scenario: Sources serving the same model slug stay distinct
- **GIVEN** model sources `src_a` and `src_b` both served model `gpt-5.1`
- **WHEN** a client filters by `upstream=src_a`
- **THEN** no `src_b` row is returned

#### Scenario: Upstream options remain expandable
- **WHEN** a user has already selected one upstream in the request logs filters
- **THEN** the upstream options continue to show other matching upstreams
- **AND** the other facets are narrowed by the selected upstream

#### Scenario: Deleted model source keeps a filterable option
- **GIVEN** request logs from model source `src_old` whose `model_sources` row was deleted
- **WHEN** the options endpoint is called
- **THEN** `upstreams` includes `src_old` with `isDeleted: true`, `displayName: null`, and the persisted kind
- **AND** the dashboard labels it with the localized deleted-source fallback including the ID
- **AND** filtering by `upstream=src_old` returns its rows

#### Scenario: Soft-deleted rows do not contribute upstream options
- **GIVEN** a model source that appears only in soft-deleted request logs
- **WHEN** the options endpoint is called
- **THEN** that source is not in `upstreams`

#### Scenario: URL-backed upstream selection
- **WHEN** a user selects upstreams in the request logs filters
- **THEN** the selection is written to the URL as repeated `upstream` parameters
- **AND** reloading the page restores the selection
- **AND** resetting the filters clears it

#### Scenario: Single observed upstream hides the control
- **GIVEN** the options response contains at most one upstream and no upstream is selected
- **WHEN** the request logs filters render
- **THEN** the Upstream filter is not shown

#### Scenario: Duplicate model-source names are disambiguated
- **GIVEN** two model sources share a display name
- **WHEN** the Upstream filter renders
- **THEN** each option label includes its source ID

#### Scenario: Older responses without upstreams
- **WHEN** the options response omits `upstreams`
- **THEN** the dashboard treats it as an empty list and hides the Upstream filter

### Requirement: Request-log rows show model-source attribution

The dashboard request-log table SHALL mark rows that carry a `modelSourceId` with a compact model-source badge, and the request details SHALL show the full model-source ID and kind. Rows without a model source MUST render as before.

#### Scenario: Model-source row badge and details
- **GIVEN** a request-log row with `modelSourceId = src_a` and `modelSourceKind = openai_compatible`
- **WHEN** the request logs table renders
- **THEN** the row shows a model-source badge
- **AND** the request details show `src_a` and `openai_compatible`

#### Scenario: Subscription row unchanged
- **GIVEN** a request-log row without `modelSourceId`
- **WHEN** the request logs table renders
- **THEN** no model-source badge or model-source detail is shown
