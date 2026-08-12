## ADDED Requirements

### Requirement: Dashboard metrics expose conversation-bearing requests

The dashboard overview response MUST expose
`summary.metrics.conversationRequests` as the count of non-warmup request-log
rows in the selected timeframe whose trimmed `conversation_id` is nonblank.
The existing `requests` field MUST continue counting all non-warmup rows, and
the existing `conversations` field MUST continue counting distinct nonblank
conversation IDs in that timeframe.

#### Scenario: Requests without conversation IDs are excluded from the new count

- **GIVEN** a timeframe contains four requests with nonblank conversation IDs
  and two requests with null or whitespace-only IDs
- **WHEN** the dashboard overview is requested
- **THEN** `conversationRequests` is `4`
- **AND** `requests` includes all six non-warmup requests

### Requirement: Dashboard conversation card shows the filtered average

The dashboard conversation card MUST be labeled `Active Conversations` with
the selected timeframe, and its secondary metadata MUST show `Avg req/conv`
followed by `conversationRequests / conversations`, formatted to one decimal
place. When `conversations` is zero, the metadata MUST show an em dash instead
of dividing by zero.

#### Scenario: Average uses only conversation-bearing requests

- **GIVEN** `conversationRequests` is `5` and `conversations` is `2`
- **WHEN** the dashboard card is rendered
- **THEN** its metadata shows `Avg req/conv 2.5`

#### Scenario: Average is safe when no conversations exist

- **GIVEN** `conversationRequests` is `4` and `conversations` is `0`
- **WHEN** the dashboard card is rendered
- **THEN** its metadata shows `Avg req/conv —`

### Requirement: Dashboard and report labels identify active conversations

The dashboard and report conversation summary cards MUST use the localized
equivalent of `Active Conversations`; their numeric values and ordering MUST
remain unchanged. The report card MUST NOT gain the dashboard average.

#### Scenario: Report uses the active-conversation label

- **WHEN** the report summary cards render
- **THEN** the conversation card label is `Active Conversations` in English
- **AND** its numeric value remains the existing distinct conversation total
- **AND** no `Avg req/conv` metadata is rendered on the report card
