## ADDED Requirements

### Requirement: Request-log listings filter by conversation ID

`GET /api/request-logs` MUST accept an optional `conversation_id` filter and
apply it together with every existing request-log filter, including timeframe,
status, model, account, API key, and search filters. The filter MUST use a bound
query parameter and MUST not change request routing or unrelated response data.

#### Scenario: Conversation-only filtering returns matching rows

- **GIVEN** request logs contain rows for `conv-a` and `conv-b`
- **WHEN** the request-log listing is requested with
  `conversation_id=conv-a`
- **THEN** only rows with conversation ID `conv-a` are returned

#### Scenario: Conversation filtering composes with existing filters

- **GIVEN** matching conversation rows differ by status, model, account, API
  key, timeframe, or search text
- **WHEN** a conversation filter and existing filters are requested together
- **THEN** every returned row matches the complete combined filter set

### Requirement: Filtered responses expose pagination-independent conversation aggregates

When `conversation_id` is present, the request-log response MUST include
`conversation.requestCount` and `conversation.aggregatedCostUsd`. Both values
MUST use the complete active filter set and MUST be independent of page limit and
offset. `aggregatedCostUsd` MUST sum stored `cost_usd` values, with no matches
represented as zero. The top-level listing total MUST remain consistent with the
filtered request count.

#### Scenario: Aggregates ignore pagination

- **GIVEN** a filtered conversation has twelve matching requests across multiple
  pages with a total stored cost of `1.23`
- **WHEN** page one and a later page are requested with different limit or
  offset values
- **THEN** both responses report `conversation.requestCount` as `12`
- **AND** both responses report `conversation.aggregatedCostUsd` as `1.23`

#### Scenario: No matching rows return zero aggregates

- **GIVEN** a conversation filter and active filters match no request logs
- **WHEN** the request-log listing is requested
- **THEN** the response reports `conversation.requestCount` as `0`
- **AND** the response reports `conversation.aggregatedCostUsd` as `0`

#### Scenario: No conversation filter returns null metadata

- **GIVEN** the request-log listing is requested without `conversation_id`
- **WHEN** the response is generated
- **THEN** the response's `conversation` metadata is null

### Requirement: Conversation filters participate in listing-count cache identity

The request-log listing-count cache signature MUST include `conversation_id` in
addition to every existing filter dimension. Requests for different
conversation IDs MUST not reuse one another's cached listing count.

#### Scenario: Different conversation IDs have isolated cached totals

- **GIVEN** two listing requests differ only by conversation ID
- **WHEN** their listing counts are served through the cache
- **THEN** each request uses its own cache entry and filtered total

### Requirement: Distinct conversation aggregates exclude null and blank IDs

Dashboard and report distinct-conversation aggregate queries MUST exclude null,
empty-string, and whitespace-only `conversation_id` values. SQL MUST use
`COUNT(DISTINCT NULLIF(TRIM(request_logs.conversation_id), ''))`, or an
equivalent database-specific expression with the same null-and-blank exclusion
semantics.

#### Scenario: Empty conversation IDs do not inflate aggregates

- **GIVEN** the active filtered range contains repeated `conv-a` values and
  rows whose conversation IDs are null, `''`, and `'   '`
- **WHEN** dashboard or report conversation aggregates are calculated
- **THEN** the distinct conversation count is `1`
- **AND** null and blank IDs do not create an unknown conversation bucket

### Requirement: Dashboard conversation trends aggregate by bucket

The dashboard conversation trend query MUST group by the configured time bucket
and count distinct non-empty normalized conversation IDs within each bucket. It
MUST exclude warmup traffic and MUST NOT use model or service-tier grouping that
could cause one conversation to be counted more than once in a bucket.

#### Scenario: One conversation across model groups counts once per bucket

- **GIVEN** a bucket contains two non-warmup request logs for `conv-a` under
  different models and one log for `conv-b`
- **WHEN** the dashboard conversation trend aggregate is calculated
- **THEN** that bucket's conversation count is `2`
