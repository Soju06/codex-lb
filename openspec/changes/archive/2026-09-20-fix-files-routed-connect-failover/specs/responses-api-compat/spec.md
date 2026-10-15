## MODIFIED Requirements

### Requirement: Pre-visible unary refresh/connect failures fail over

For unary proxy requests that have not emitted downstream-visible output, the proxy MUST treat retryable token-refresh or upstream-connect transport failures as account-local transient failures.

This applies to Codex thread-goal requests, Codex control requests,
transcription requests, and file create/finalize requests. When another
eligible account is available within the request budget, the proxy MUST record
the failed account, exclude it from the current request, and retry the unary
operation on the fallback account. The proxy MUST NOT fail over strict
account-owner requests whose upstream resource is bound to the selected account.

For account-routed file operations, the client and service MUST preserve typed transport phase and replay eligibility. Confirmed pre-dispatch connection failures MUST use the existing account failover policy even when the credential-safe message contains no transient-error phrase. Typed transport errors MUST NOT gain replay eligibility from message text. TLS verification failures, ambiguous request failures, response-body failures, and process-wide network failures MUST NOT cause cross-account file retries.

#### Scenario: Unary refresh transport failure uses another account

- **GIVEN** at least two accounts are eligible for a Codex thread-goal, Codex
  control, transcription, or file-create request
- **AND** the selected account fails during token refresh or upstream connect
  with a retryable transient transport error before downstream-visible output
- **WHEN** another eligible account can complete the request within the request
  budget
- **THEN** the downstream request succeeds from the fallback account
- **AND** the failed account is recorded and excluded from further attempts for
  that request

#### Scenario: Strict file-owner refresh failure fails closed

- **GIVEN** a file-finalize request is pinned to the account that owns the file
- **AND** the pinned account fails during token refresh or upstream connect with
  a retryable transient transport error before downstream-visible output
- **WHEN** another account would otherwise be eligible for proxy traffic
- **THEN** the proxy fails the request with an upstream-unavailable error
- **AND** the proxy does not send the file-finalize operation through another
  account

#### Scenario: Routed file connection refusal uses another account

- **GIVEN** an unpinned file-create request with another eligible account
- **WHEN** the routed transport proves that the selected account's proxy connection failed before dispatch
- **THEN** the proxy excludes that account and completes the file-create request through the eligible fallback within the existing budget

#### Scenario: Routed file replay is denied without safe provenance

- **GIVEN** a routed file request with another eligible account
- **WHEN** the transport reports a TLS verification failure, ambiguous request failure, response-body failure, or process-wide network failure
- **THEN** the proxy returns the transport error without invoking the file operation through another account
- **AND** transient-looking text in the sanitized message does not permit replay
