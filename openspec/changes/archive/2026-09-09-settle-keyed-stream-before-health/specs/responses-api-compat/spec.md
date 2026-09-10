## ADDED Requirements

### Requirement: Ordered owner-unavailable settlement preserves external errors

Settlement ordering for streaming Responses owner-unavailable failures MUST NOT
change the downstream or request-log error envelope. The client and request log
MUST continue to use `previous_response_owner_unavailable`, including when the
original upstream code is retained for account-health recovery.

#### Scenario: Ordered rewrite keeps the public and logged classifier

- **WHEN** a streaming `/v1/responses` failure is rewritten after ordered
  reservation settlement
- **THEN** the downstream error code is `previous_response_owner_unavailable`
- **AND** the request-log error code is `previous_response_owner_unavailable`
- **AND** no raw stale-anchor identifier or source-ownership detail is exposed
