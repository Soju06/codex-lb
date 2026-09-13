## ADDED Requirements

### Requirement: Request logs surface reasoning-token usage

The dashboard request-log API and UI MUST preserve and render the upstream-provided reasoning-token count separately from the inclusive output-token count. The UI MUST treat reasoning tokens as a subset of output tokens and MUST NOT add them to total-token or cost calculations.

#### Scenario: Request row shows an available reasoning count

- **GIVEN** a request-log row has `outputTokens=200` and `reasoningTokens=80`
- **WHEN** the dashboard renders the recent-requests table
- **THEN** the token cell shows 80 reasoning tokens as secondary metadata
- **AND** the row's total-token value remains input tokens plus 200 output tokens

#### Scenario: Request detail identifies the reasoning subset

- **GIVEN** a request-log row has a persisted reasoning-token count
- **WHEN** the operator opens `View Details`
- **THEN** the dialog renders the exact reasoning-token count
- **AND** its label identifies the count as included in output tokens

#### Scenario: Missing reasoning usage is not estimated

- **GIVEN** a request-log row has `reasoningTokens=null`
- **WHEN** the dashboard renders the row and its details
- **THEN** the dashboard does not derive a reasoning count from output text, reasoning summaries, or total output tokens

### Requirement: Reports expose reasoning-token totals

`GET /api/reports` MUST expose the sum of reported reasoning counts as `totalReasoningTokens` in its summary and nullable `reasoningTokens` in each daily row, using the same date, account, model, and user-agent filters as the existing token totals. The summary MUST expose `reasoningUsageKnownRequests`, counting rows whose upstream reasoning count is known, including known zeroes. The reports UI MUST label the aggregate as reported reasoning, show its known-request coverage, render it in the daily breakdown, and export it in the daily CSV. A daily row with requests but no reported reasoning counts MUST preserve `reasoningTokens=null`; known zero MUST remain zero. Existing total-token comparisons MUST remain input tokens plus inclusive output tokens, with a stored reasoning count serving as the output fallback when an older row has no output total.

#### Scenario: Reports aggregate reasoning usage

- **GIVEN** eligible request logs in a report window contain reasoning-token counts of 30 and 70 and one request with an unknown count
- **WHEN** an operator requests that report window
- **THEN** `summary.totalReasoningTokens` is 100
- **AND** `summary.reasoningUsageKnownRequests` is 2
- **AND** each daily row's `reasoningTokens` is the sum for that local-calendar day

#### Scenario: Reports identify reasoning as an output subset

- **GIVEN** a report summary has 1,000 input tokens, 400 output tokens, and 250 reasoning tokens
- **WHEN** the dashboard renders the token summary
- **THEN** the total remains 1,400 tokens
- **AND** the summary identifies 250 reasoning tokens as included in the 400 output tokens

#### Scenario: Daily CSV exports reasoning tokens

- **WHEN** an operator exports the reports daily breakdown
- **THEN** the CSV contains a Reported Reasoning Tokens column
- **AND** every row contains that day's reasoning-token aggregate

#### Scenario: A daily aggregate has no reported reasoning usage

- **GIVEN** a report day contains requests whose reasoning-token counts are all unknown
- **WHEN** the reports API and dashboard render that day
- **THEN** the daily `reasoningTokens` value remains null
- **AND** the table and CSV do not present it as a known zero

#### Scenario: A legacy row has reasoning usage but no output total

- **GIVEN** an eligible request log has `outputTokens=null` and `reasoningTokens=40`
- **WHEN** the report aggregates inclusive output tokens
- **THEN** the row contributes 40 output tokens and 40 reported reasoning tokens
- **AND** reasoning is not added to that output total a second time
