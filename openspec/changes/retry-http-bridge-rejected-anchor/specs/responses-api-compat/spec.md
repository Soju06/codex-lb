## MODIFIED Requirements

### Requirement: HTTP bridge stale previous-response anchors recover safely
The service MUST handle an HTTP bridge upstream `previous_response_not_found`
for a previous-response anchor that the current session sent by first clearing
that rejected anchor through the existing fenced session-anchor clear. If the
clear succeeds and the pending request has an existing replay-safe fresh
upstream body, the service MUST retry the same logical turn once without
`previous_response_id` instead of surfacing the first terminal error
downstream. The retry MUST use the existing replay-safety decision and
fresh-upstream replay path.

#### Scenario: Replay-safe rejected anchor completes after retry
- **GIVEN** an HTTP bridge request is sent with `previous_response_id`
- **AND** the request has a replay-safe fresh upstream body without that anchor
- **WHEN** upstream rejects the anchor with `previous_response_not_found`
- **AND** the fenced rejected-anchor clear succeeds
- **THEN** codex-lb retries the same logical turn once without `previous_response_id`
- **AND** the downstream client receives the retry result instead of the first terminal error

#### Scenario: Unsafe rejected anchor remains fail-closed
- **GIVEN** an HTTP bridge request is sent with `previous_response_id`
- **AND** the request does not have a replay-safe fresh upstream body
- **WHEN** upstream rejects the anchor with `previous_response_not_found`
- **THEN** codex-lb MUST NOT silently replay the request
- **AND** the request follows the existing fail-closed continuity path

#### Scenario: Rejected-anchor retry does not loop
- **GIVEN** an HTTP bridge request has already consumed its fresh rejected-anchor retry
- **WHEN** upstream again returns `previous_response_not_found` for the same logical turn
- **THEN** codex-lb MUST NOT retry again
- **AND** the request follows the existing fail-closed continuity path

#### Scenario: Fence miss blocks rejected-anchor retry
- **GIVEN** a newer owner has already replaced the session anchor
- **WHEN** the rejected-anchor clear is declined by the durable owner fence
- **THEN** codex-lb MUST NOT clear or retry over the newer owner's anchor
- **AND** the request follows the existing fail-closed continuity path
