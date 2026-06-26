## MODIFIED Requirements

### Requirement: Request logs expose cost breakdown details

When a request log has sufficient usage data, the dashboard request-log API MUST expose raw input/output token counts and a cost breakdown that separates non-cached input, cached input, and output cost. Request-log rows MAY include backend-preserved request-kind strings beyond the dashboard's known label set. The dashboard client MUST parse any string `requestKind`, default omitted request kinds to `normal`, and MUST NOT reject the full request-log response solely because a request kind is new to the frontend. For known non-normal request kinds such as `warmup`, `limit_warmup`, `prewarm`, and `compaction`, the dashboard SHOULD render a readable label; for unknown non-normal values, it MAY render the raw string.

#### Scenario: Successful request log exposes token and cost segments

- **WHEN** a successful request log row has persisted input, cached-input, and output usage
- **THEN** `GET /api/request-logs` includes `inputTokens`, `outputTokens`, and `costBreakdown`
- **AND** `costBreakdown` includes `inputUsd`, `cachedInputUsd`, `outputUsd`, and `totalUsd`

#### Scenario: Request log output falls back to reasoning tokens

- **WHEN** a successful request log row has no persisted `output_tokens` and does have `reasoning_tokens`
- **THEN** `GET /api/request-logs` uses the reasoning-token value for `outputTokens`

#### Scenario: Request log response preserves shape for legacy partial data

- **WHEN** a successful request log row is missing one or more persisted token or cost segments
- **THEN** `GET /api/request-logs` still includes `inputTokens`, `outputTokens`, and `costBreakdown`
- **AND** any unavailable top-level token field is returned as `null`
- **AND** `costBreakdown` includes `inputUsd`, `cachedInputUsd`, `outputUsd`, and `totalUsd`
- **AND** any unavailable `costBreakdown` field is returned as `null`
- **AND** clients can render only the available token and cost segments without treating the row as invalid

#### Scenario: Prewarm request kind does not break request logs

- **WHEN** `GET /api/request-logs` includes a row with `requestKind: "prewarm"`
- **THEN** the dashboard parses the response successfully
- **AND** the recent-requests UI identifies the row as `Prewarm`

#### Scenario: Future request kind does not break request logs

- **WHEN** `GET /api/request-logs` includes a row with a non-empty request-kind string that the dashboard does not label explicitly
- **THEN** the dashboard parses the response successfully
- **AND** the recent-requests UI can render the raw request-kind string instead of failing the whole table
