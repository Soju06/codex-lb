## ADDED Requirements

### Requirement: Dashboard conversation listing

The authenticated dashboard MUST expose `GET /api/conversations`. The list
endpoint MUST accept only `limit`, `offset`, and `search` query parameters. It
MUST aggregate eligible `request_logs` rows by normalized, non-empty
`conversation_id`, excluding rows whose request kind is `warmup` or
`limit_warmup`, and rows with `deleted_at IS NOT NULL`.

Search MUST be case-insensitive and match the normalized conversation ID or any
eligible row's user-agent family. Search MUST select whole conversations first:
after a conversation matches, aggregation MUST include all eligible rows in that
conversation, including rows whose user-agent family or ID did not match the
search text. The endpoint MUST derive aggregates from `request_logs` only.

The response MUST contain `conversations`, `total`, and `hasMore` pagination
fields. Each row in `conversations` MUST contain exactly these fields and no
response summary object:

- `conversationId`: the normalized, non-empty conversation identity.
- `lastRequest`: the latest `requested_at` among all eligible rows in the
  conversation.
- `representativeAccount` and `remainingAccountCount`.
- `apiKeyId` and `apiKeyName`.
- `representativeModel` and `remainingModelCount`.
- `totalTokens`.
- `cachedInputTokens`.
- `totalCostUsd`.

The camelCase names above are the external Dashboard API JSON contract. Python
schema, service, and repository identifiers MAY remain snake_case internally;
internal names MUST NOT be emitted as alternate response fields.

`totalTokens` MUST equal total input tokens plus total output tokens, with
`reasoning_tokens` used for a row when `output_tokens` is null.
`cachedInputTokens` MUST use the existing per-row clamp: null remains null;
otherwise the cached value is clamped to `[0, input_tokens]` when input tokens
are present. At aggregate level, null per-row values MUST NOT be converted to
zero; when every eligible row has a null cached value, `cachedInputTokens` MUST
be null, and otherwise it MUST equal the sum of the known clamped values.

Representative account values MUST use `request_count DESC,
latest_requested_at DESC, lexical account ASC`. List model values MUST be
grouped by distinct model, combining all reasoning efforts for that model, and
the representative model MUST use `request_count DESC, latest_requested_at DESC,
model lexical ASC`. Null account values MUST be excluded from account
candidates; if no non-null account exists, `representativeAccount` MUST be null
and `remainingAccountCount` MUST be 0. The list MUST NOT split model values by
`reasoning_effort`; `(model, reasoning_effort)` grouping MUST be used only for
conversation details.

Nullable and multiple-key conversations MUST be handled deterministically. Null
API-key values MUST not be candidates; if no non-null key exists, both API-key
fields MUST be null. When multiple distinct non-null keys exist, `apiKeyId` MUST be selected by
`request_count DESC, latest_requested_at DESC, lexical API-key ID ASC`, and
`apiKeyName` MUST be the corresponding existing dashboard-safe display name.
`apiKeyName` MUST never expose a secret, hash, or plaintext key material.

The list order MUST be stable: `lastRequest DESC`, then normalized
`conversationId ASC`. Pagination MUST be applied after this ordering.

#### Scenario: Pagination uses the stable list order

- **GIVEN** matching conversations have different latest request times and a
  tie exists on `lastRequest`
- **WHEN** the client calls `GET /api/conversations?limit=10&offset=20`
- **THEN** rows are ordered by `lastRequest DESC` and ties by normalized
  `conversationId ASC`
- **AND** the response starts at the 21st row in that order and reports the
  matching total and whether another page exists

#### Scenario: Blank IDs, warmups, and soft-deleted rows are excluded

- **GIVEN** request logs include null IDs, whitespace-only IDs, `warmup` rows,
  `limit_warmup` rows, soft-deleted rows, and eligible rows with non-empty IDs
- **WHEN** the client calls `GET /api/conversations`
- **THEN** only rows whose request kind is neither `warmup` nor `limit_warmup`,
  which are non-soft-deleted and have non-empty normalized IDs, contribute to
  returned conversations

#### Scenario: Search selects whole conversations

- **GIVEN** one eligible conversation contains a matching user-agent family on
  one row and non-matching user-agent/ID values on other rows
- **WHEN** the client calls `GET /api/conversations?search=opencode`
- **THEN** that conversation is selected
- **AND** all eligible rows in that conversation contribute to its counts,
  tokens, cached tokens, and cost
- **AND** rows from conversations with no matching ID or user-agent family are
  not returned

#### Scenario: List search is case-insensitive over normalized IDs and user-agent families

- **GIVEN** an eligible conversation has a normalized ID and user-agent family
  whose letters differ in case from the search text
- **WHEN** the client calls `GET /api/conversations?search=OPENCODE`
- **THEN** the conversation is selected when either the normalized ID or any
  eligible row's user-agent family matches case-insensitively

#### Scenario: List model representatives ignore reasoning effort

- **GIVEN** a conversation has requests for the same model with multiple
  reasoning-effort values and requests for another model
- **WHEN** the client calls `GET /api/conversations`
- **THEN** the list groups the same model's requests into one model value
- **AND** the representative model is ordered by request count descending,
  latest request descending, and model lexical ascending
- **AND** the remaining model count counts distinct models, not model/effort
  combinations

#### Scenario: API-key representation is safe and deterministic

- **GIVEN** a conversation has null API-key rows and multiple non-null API-key
  values with tied counts
- **WHEN** the client calls `GET /api/conversations`
- **THEN** null values do not become the representative
- **AND** the non-null representative is selected by count, latest request, and
  lexical API-key ID
- **AND** the response contains only the corresponding dashboard-safe name and
  never secret, hash, or plaintext key material

### Requirement: Conversation details

The authenticated dashboard MUST expose
`GET /api/conversations/{conversation_id}`. Detail aggregation MUST use the same
eligible-row scope as listing: normalized non-empty IDs, rows whose request kind
is neither `warmup` nor `limit_warmup`, and `deleted_at IS NULL`.

For a matching conversation, the detail response MUST expose the conversation ID,
`start` (earliest `requested_at`), `latest` (latest `requested_at`),
`accountCount` (distinct non-null accounts), `totalElapsedTime`, and
`dominantUseragentGroup`. `totalElapsedTime` MUST be
`SUM(COALESCE(latency_ms, 0))` over all eligible rows, never the wall-clock span.
`dominantUseragentGroup` MUST use
`request_count DESC, latest_requested_at DESC, lexical ASC`.

The response MUST include one model/effort row per distinct
`(model, reasoning_effort)` combination. Each row MUST contain exactly:
`modelEffort`, `reqs`, `totalElapsedTime`, `totalInputTokens`,
`cachedInputTokens`, `totalOutputTokens`, and `totalCostUsd`. The row
elapsed time MUST use `SUM(COALESCE(latency_ms, 0))` for that combination;
output tokens MUST use the reasoning-token fallback; cached input MUST use the
existing per-row clamp. No error-count or other column may be returned.

The API MUST order model/effort rows by `reqs DESC`, latest request DESC, and
lexical key ASC. It MUST NOT accept a sort query parameter. Client-side sorting
MUST operate only on returned rows.

An encoded blank path such as `GET /api/conversations/%20` MUST return the
project-standard 404 response. An unknown non-empty conversation ID MUST also
return the project-standard 404 response. The detail route MUST accept any
normalized non-empty stored conversation ID, including IDs containing `/`, when
the client percent-encodes the opaque ID as one path value.

#### Scenario: Details preserve cumulative elapsed time

- **GIVEN** a conversation has known latencies across multiple accounts and
  model/effort combinations
- **WHEN** the client calls `GET /api/conversations/conv-a`
- **THEN** conversation `totalElapsedTime` is the sum of
  `COALESCE(latency_ms, 0)` across eligible rows
- **AND** each model/effort row uses the same cumulative sum over its matching
  rows rather than the start/latest wall-clock span

#### Scenario: Details exclude warmups and soft-deleted rows

- **GIVEN** a conversation contains normal, `warmup`, `limit_warmup`, and
  soft-deleted request logs
- **WHEN** the client calls `GET /api/conversations/conv-a`
- **THEN** the summary and every model/effort row include only rows whose request
  kind is neither `warmup` nor `limit_warmup` and which are non-soft-deleted

#### Scenario: Blank and unknown detail IDs use standard not-found behavior

- **WHEN** the client calls `GET /api/conversations/%20` or requests an unknown
  non-empty ID
- **THEN** the API returns the standard 404 error envelope

#### Scenario: Slash-containing detail IDs remain addressable

- **GIVEN** an eligible conversation has the normalized ID `workspace/thread-1`
- **WHEN** the client calls `GET /api/conversations/workspace%2Fthread-1`
- **THEN** the API returns that conversation's details with
  `conversationId` equal to `workspace/thread-1`

### Requirement: Dashboard conversation view

The dashboard MUST render Request Logs by default. The original uppercase
section-title typography MUST be retained, and the title itself MUST be the
single accessible Radix-style selector trigger with `ChevronDown` for Request
Logs and Conversations. A separate selector MUST NOT render to the title's
right. Selecting Conversations MUST persist `view=conversations` in the URL;
selecting Request Logs MUST return to the existing request-log view.

The dashboard MUST retain separate URL-backed query state for Request Logs and
Conversations, including each view's applicable filters and pagination.
Switching views MUST NOT reinterpret, overwrite, or clear the inactive view's
query state, and returning to a view MUST restore its retained state.

The Conversations view MUST NOT render a filter input above the list and MUST
NOT provide date/timeframe controls. The view MUST use the list endpoint's
established loading, error, empty, and pagination behavior.

The conversation list MUST render exactly these columns in order: Last request,
Conversation, Accounts, API key, Models, Tokens, Cost, and Details. Last request
MUST use the request-log Time column's two-line time/date presentation. Accounts
MUST resolve the representative account ID through the dashboard account
summaries and display `displayName`, then email, then the ID as a final fallback.
Accounts and models MUST render remaining values as a smaller muted `+ N more`
secondary line. Tokens MUST show total tokens with cached input tokens on a
subordinate line.
When dashboard privacy blur is enabled, an account label resolved from an email
fallback MUST render with the established `privacy-blur` class; display-name
and account-ID fallback labels MUST remain unblurred.
The API-key column MUST use `apiKeyName` only. Details MUST use the existing
Details button treatment.

The details dialog MUST render row one as conversation ID, start, and latest;
row two as account count, total elapsed time, and dominant user-agent family;
and a model/effort table with exactly these displayed columns, in order: Model
(effort), Reqs, Total elapsed, Total input (with total cache as a
subordinate/parenthetical value), Total output, and Total cost. Total cache MUST
not be a separate displayed column. The table MUST default to Reqs descending
and MUST support client-side sorting for every displayed column without adding a
sort query parameter.
The displayed conversation ID MUST NOT provide a copy action.

The detail dialog MUST use the established dashboard loading state while the
detail API is pending. Unknown or malformed conversation IDs, including a
standard detail API 404, MUST use the standard dashboard error display and retry
behavior. Nullable optional aggregate values MUST render the established
em-dash or other dashboard fallback value without breaking the row or dialog.
An empty conversation list MUST render the established dashboard empty state.

#### Scenario: Request Logs is the default and selector switches views

- **WHEN** an operator opens the dashboard
- **THEN** Request Logs is visible and active by default
- **WHEN** the operator selects Conversations
- **THEN** the Conversations list renders and the URL contains
  `view=conversations`

#### Scenario: Request Logs and Conversations retain independent URL query state

- **GIVEN** Request Logs has active filters and pagination and Conversations has
  different active filters and pagination retained in the URL
- **WHEN** the operator switches between the two views
- **THEN** each view restores its own filters and pagination
- **AND** switching views does not reinterpret, overwrite, or clear the other
  view's query state

#### Scenario: Conversations has no filter input and uses exact rendering

- **WHEN** the operator opens the Conversations view
- **THEN** no filter input or date/timeframe controls are rendered above the list
- **AND** the list renders the specified reordered columns and two-line request
  time presentation
- **AND** representative account IDs resolve to display name, then email, then ID
- **AND** smaller muted `+ N more` account/model secondary lines and cached
  tokens as a subordinate line are rendered

#### Scenario: Conversation account privacy blur applies only to email fallback

- **GIVEN** dashboard privacy blur is enabled and account labels resolve using
  display name, email fallback, and account-ID fallback values
- **WHEN** the Conversations list renders
- **THEN** only the email-fallback label has the established `privacy-blur` class
- **AND** the display-name and account-ID fallback labels remain unblurred

#### Scenario: The original-styled title is the only view selector

- **WHEN** the list section renders
- **THEN** its uppercase title typography is retained
- **AND** activating the title opens the Request Logs/Conversations selector
- **AND** no separate selector is rendered to the title's right

#### Scenario: Conversation details use established loading and retry states

- **WHEN** the detail API is loading for a selected conversation
- **THEN** the dialog uses the established dashboard loading state
- **WHEN** the detail API returns an unknown or malformed-ID error
- **THEN** the dialog uses the standard dashboard error display with retry

#### Scenario: Nullable detail aggregates use dashboard fallbacks

- **GIVEN** a successful detail response contains nullable optional aggregate
  values
- **WHEN** the operator opens the details dialog
- **THEN** each nullable value renders the established em-dash or dashboard
  fallback without breaking the row or dialog

#### Scenario: Empty conversation results use the existing empty state

- **GIVEN** the conversation list response contains no rows
- **WHEN** the operator opens the Conversations view
- **THEN** the existing dashboard empty state is rendered

#### Scenario: Details dialog has the approved layout and sorting

- **WHEN** the operator opens a conversation's Details dialog
- **THEN** row one contains conversation ID/start/latest
- **AND** conversation ID has no copy action
- **AND** row two contains account count/total elapsed/dominant user-agent
- **AND** the table displays exactly Model (effort), Reqs, Total elapsed, Total
  input (with total cache as a subordinate/parenthetical value), Total output,
  and Total cost
- **AND** the table initially sorts by Reqs descending
- **AND** activating any displayed table column header reorders only the returned
  rows client-side
