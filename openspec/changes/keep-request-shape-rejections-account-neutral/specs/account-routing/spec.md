## ADDED Requirements

### Requirement: Upstream rejections of the request payload are account neutral

When upstream rejects a request because of the request payload itself, the proxy MUST NOT mutate the selected account's health: it MUST NOT record a transient account error, a rate-limit penalty, a quota penalty, or a permanent failure for that account. An upstream failure qualifies as a payload rejection only when it would reproduce identically on every account. The proxy MUST decide membership from the classified upstream message, never from the `invalid_request_error` code alone, and MUST require the upstream HTTP status to be 400 whenever a status is known. An upstream missing-tool-output rejection — the `invalid_request_error` whose message identifies a tool call with no matching tool output — MUST qualify. An account-scoped `invalid_request_error`, including the model-entitlement rejection `The '<model>' model is not supported when using Codex with a ChatGPT account.`, MUST NOT qualify and MUST keep its existing account-health handling. Skipping the penalty MUST be logged so the decision is observable, and MUST NOT change the failure classification, the failover decision, or the status and body returned to the client.

#### Scenario: Missing-tool-output rejection leaves account health untouched

- **GIVEN** account A is selected for a request whose input references a tool call with no matching tool output
- **WHEN** upstream returns HTTP 400 `invalid_request_error` with a missing-tool-output message
- **THEN** the proxy does not increment account A's transient error count and does not mark it rate-limited, quota-exceeded, or permanently failed
- **AND** the failure is still classified `non_retryable` and surfaced to the client unchanged

#### Scenario: Repeated client payload rejection cannot starve unrelated sessions

- **GIVEN** one client repeatedly re-sends the same payload that upstream rejects for a missing tool output
- **WHEN** those requests are served by accounts shared with other sessions
- **THEN** no serving account enters error backoff because of that payload
- **AND** a session hard-pinned to one of those accounts is not failed with a saturated-hard-affinity selection error caused by that payload

#### Scenario: Model-entitlement rejection still penalizes the account

- **GIVEN** account A cannot use the requested model
- **WHEN** upstream returns HTTP 400 `invalid_request_error` stating the model is not supported for a ChatGPT account
- **THEN** the proxy records the account-health penalty for account A as before
