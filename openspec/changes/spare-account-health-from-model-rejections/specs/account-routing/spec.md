## MODIFIED Requirements

### Requirement: Upstream rejections of the request payload are account neutral

When upstream rejects a request because of the request payload itself, the proxy MUST NOT mutate the selected account's health: it MUST NOT record a transient account error, a rate-limit penalty, a quota penalty, or a permanent failure for that account. An upstream failure qualifies as a payload rejection only when it would reproduce identically on every account. The proxy MUST decide membership from the classified upstream message, never from the `invalid_request_error` code alone, and MUST require the upstream HTTP status to be 400 whenever a status is known. An upstream missing-tool-output rejection — the `invalid_request_error` whose message identifies a tool call with no matching tool output — MUST qualify. The proxy MUST also leave account health untouched for the model-entitlement rejection `The '<model>' model is not supported when using Codex with a ChatGPT account.`: that rejection is scoped to the named model and is not evidence about the account's ability to serve the models it is entitled to. Because upstream delivers that rejection on the streaming path with neither an error `code` nor an error `type`, which normalizes to the `upstream_error` fallback, the proxy MUST decide it from the message and the 400 status alone and MUST NOT require a particular normalized error code. Skipping the penalty MUST be logged so the decision is observable, and MUST NOT change the failure classification, the failover decision, or the status and body returned to the client.

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

#### Scenario: Model-entitlement rejection leaves account health untouched

- **GIVEN** account A is selected for a model it is not entitled to use
- **WHEN** upstream returns HTTP 400 stating the model is not supported when using Codex with a ChatGPT account, with the error code normalized to `upstream_error` or to `invalid_request_error`
- **THEN** the proxy does not increment account A's transient error count and does not mark it rate-limited, quota-exceeded, or permanently failed
- **AND** the skip is logged

#### Scenario: Model-entitlement rejection still fails over

- **GIVEN** account A returned the model-entitlement rejection for the requested model
- **WHEN** the proxy classifies that failure
- **THEN** the classification and failover decision are unchanged, so an account with a different entitlement is still attempted
- **AND** the status and body returned to the client when every attempt is exhausted are unchanged

#### Scenario: A model no source can serve cannot poison subscription accounts

- **GIVEN** a model that resolves to no enabled model source and therefore reaches subscription account selection
- **WHEN** a client polls that model repeatedly and every subscription account returns the model-entitlement rejection
- **THEN** no serving account enters error backoff because of those rejections
- **AND** unrelated traffic hard-pinned to those accounts is not denied with a continuity-owner-unavailable or no-available-accounts selection error caused by them

#### Scenario: Model-entitlement rejection still penalizes the account

- **GIVEN** account A is selected for a request
- **WHEN** upstream fails with a non-400 status whose message matches the model-entitlement rejection
- **THEN** the proxy records the account-health penalty for account A as before, because only a genuine HTTP 400 qualifies as the model-scoped rejection

#### Scenario: A genuine upstream failure still penalizes the account

- **GIVEN** account A is selected for a request
- **WHEN** upstream fails with an `upstream_error` whose message is not the model-entitlement rejection
- **THEN** the proxy records the account-health penalty for account A as before
