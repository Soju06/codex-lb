## ADDED Requirements

### Requirement: Request details expose conversation filtering

The request-details dialog MUST render Client IP and Conversation ID in one
shared row. When present, the conversation ID MUST be a clickable semantic text
control without an underline. Activating it MUST close the dialog, preserve all
existing request-log filters, set the clicked conversation ID as the active
URL-backed conversation filter, and reset pagination.

#### Scenario: Clicking a conversation filters the request log

- **GIVEN** request details display conversation ID `conv-a` while other filters
  are active
- **WHEN** the conversation ID is activated
- **THEN** the dialog closes
- **AND** the other filters remain active
- **AND** the active URL-backed conversation filter becomes `conv-a`
- **AND** pagination resets

### Requirement: Conversation filter state is removable and summarized

When a conversation filter is active, the dashboard MUST render a removable
conversation badge between the Statuses control and Reset. Dismissing the badge
MUST clear only the conversation filter and reset pagination. When the filtered
API response includes conversation metadata, the dashboard MUST render a
summary box between the filter row and request-log table with the form:

`The conversation ${id} runs ${count} request(s), cost = ${formattedCost}`. The ID, count, and cost MUST be separate styled inline-code values without literal backticks.

If at least one other non-conversation filter is active, the summary MUST append
an inline suffix describing those active filters and MUST omit the conversation
filter from that suffix. If no other non-conversation filter is active, the
summary MUST omit the suffix. The response-level `conversation` metadata MUST
contain only `requestCount` and `aggregatedCostUsd`; it MUST NOT duplicate the
conversation ID because the active URL-backed filter already identifies it.

#### Scenario: Dismissing the badge clears only conversation state

- **GIVEN** the conversation badge and other request-log filters are active
- **WHEN** the badge is dismissed
- **THEN** only the conversation filter is cleared
- **AND** pagination resets
- **AND** the other filters remain active

#### Scenario: Summary describes the active filtered conversation

- **GIVEN** the active URL-backed conversation filter is `conv-a` and the
  filtered response contains
  `conversation: { requestCount: 12, aggregatedCostUsd: 1.23 }`, with timeframe
  and status filters also active
- **WHEN** the request-log page renders
- **THEN** the summary appears between the filter row and table
- **AND** it states the active conversation ID, count, and formatted cost
- **AND** its inline suffix describes the timeframe and status without repeating
  the conversation filter
- **AND** the response-level conversation metadata contains exactly
  `requestCount` and `aggregatedCostUsd`, with no ID field

#### Scenario: Summary omits suffix without other filters

- **GIVEN** the active URL-backed conversation filter is `conv-a` and no other
  non-conversation filter is active
- **WHEN** the request-log page renders
- **THEN** the summary contains the conversation sentence without an inline
  filter suffix

### Requirement: Dashboard and report metrics count distinct conversations

Dashboard overview metrics MUST include a Conversations card between Est. API
Cost and Error Rate, counting distinct non-empty conversation IDs in the
selected timeframe. Report summary metrics MUST include a Conversations card
immediately after Requests, counting distinct non-empty IDs across the complete
filtered report range. A conversation spanning multiple days MUST count once in
each applicable daily row and once in the report-wide total. Neither card MUST
render a `{count} distinct` secondary label.

#### Scenario: Dashboard count deduplicates IDs

- **GIVEN** the selected dashboard timeframe contains repeated, null, and empty
  conversation IDs
- **WHEN** overview metrics are rendered
- **THEN** the Conversations card counts each distinct non-empty ID once
- **AND** the card is between Est. API Cost and Error Rate

#### Scenario: Report summary and daily counts use distinct IDs

- **GIVEN** one conversation has requests on two report days and another has
  requests on one day
- **WHEN** report metrics are rendered
- **THEN** the summary counts two distinct conversations overall
- **AND** each applicable daily row counts the spanning conversation once
- **AND** the Conversations summary card is immediately after Requests

### Requirement: Report conversation columns sort and export

The daily report table MUST place a Conversations column between Reqs and Input
Tokens. The column MUST support numeric sorting and CSV export, and its values
MUST preserve the distinct non-empty daily conversation counts, including
zero-filled days.

#### Scenario: Daily conversation values are sortable and exported

- **GIVEN** daily report rows have different conversation counts, including a
  zero-count row
- **WHEN** the Conversations column is sorted or CSV export is generated
- **THEN** sorting is numeric by conversation count
- **AND** the column appears between Reqs and Input Tokens
- **AND** CSV output contains the Conversations header and each row's value

### Requirement: Dashboard conversation trends are bucketed distinctly

The dashboard overview response MUST expose `trends.conversations` with one
point for each configured timeframe bucket. Each point MUST count distinct,
non-empty conversation IDs within that bucket, and a conversation repeated
across models or service tiers in one bucket MUST count once. Missing buckets
MUST be zero. The Conversations card MUST use this series, while its summary
total MUST remain the exact timeframe aggregate rather than a sum of trend
points.

#### Scenario: Conversation trend de-duplicates model groups

- **GIVEN** one bucket contains the same conversation ID under two models and a
  second distinct conversation ID under one model
- **WHEN** the dashboard overview trends are rendered
- **THEN** the populated bucket's conversation point is `2`
- **AND** the series contains one point per configured bucket

#### Scenario: Empty conversation buckets are zero-filled

- **GIVEN** the selected dashboard timeframe has no valid conversation IDs in
  one or more buckets
- **WHEN** the dashboard overview response is built
- **THEN** each empty bucket's conversation point is `0`

#### Scenario: Conversation summary values use inline code formatting

- **GIVEN** a filtered conversation has ID `ses_123`, request count `35`, and
  formatted cost `$0.74`
- **WHEN** the conversation summary box renders
- **THEN** it uses the copy `The conversation ses_123 runs 35 request(s), cost =
  $0.74`
- **AND** the ID, count, and cost are separate styled inline-code values
- **AND** literal backtick characters are absent
