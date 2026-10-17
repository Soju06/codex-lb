## ADDED Requirements

### Requirement: Direct usage queries prefer native egress with explicit ownership

After the existing route-or-direct authorization check, a usage GET without a
resolved route or explicit Python RetryClient MUST prefer the discovered native
helper. An explicit Python client MUST bypass native discovery. Routed usage
MUST retain CodexClient ownership. Native requests MUST preserve the usage URL,
authentication/account/request-id headers, default compression negotiation and
effective total timeout, and MUST
use the existing native HTTP environment-proxy resolver. Python MUST retain
payload validation, public error mapping and retry policy.

#### Scenario: Explicit client and route retain precedence

- **WHEN** a usage call supplies a resolved route or a direct Python client
- **THEN** it uses that route or client without direct native discovery
- **AND** an unauthorized direct call fails before any helper discovery or network call

#### Scenario: Missing helper permits only initial fallback

- **WHEN** no helper is found or the first native request raises helper-unavailable before dispatch
- **THEN** the original Python transport handles the GET
- **AND** protocol incompatibility, body failure, or helper loss after a prior attempt MUST NOT select Python fallback

### Requirement: Native usage preserves direct retry and response failure semantics

Native usage GETs MUST use the existing direct retryable HTTP statuses, maximum
attempt count and one-based ExponentialRetry delay calculation. Retryable status
responses MUST be closed before backoff without waiting for their body. Native
transport failures before response headers MAY use the remaining GET attempts;
protocol failures and final-response body failures MUST NOT be retried. Native
transport/protocol failures MUST surface as UsageFetchError with status 0.

For a fully read response, non-JSON error text and non-object JSON MUST retain
the existing direct Python error mapping, including charset and empty-body
handling. Compressed responses MUST be decoded as with the default Python client.
Malformed success payloads MUST retain
the 502 invalid-usage-payload result. Cancellation MUST propagate after exchange
cleanup and MUST NOT invalidate other requests sharing the helper.

#### Scenario: Status retry does not depend on body completion

- **WHEN** a retryable usage response sends headers but stalls its body and an attempt remains
- **THEN** its exchange is closed and the next native GET follows the existing backoff
- **AND** the same effective per-attempt timeout and attempt cap apply

#### Scenario: Response-body failure remains a transport error

- **WHEN** the selected response body is truncated or times out
- **THEN** the call raises UsageFetchError with status 0 without retry or Python fallback
- **AND** fully read plain-text error responses preserve their text instead

#### Scenario: Encoded responses match the default Python client

- **WHEN** the upstream returns compressed JSON or a body with an explicit charset
- **THEN** native usage decodes it and produces the same payload or error as direct Python usage
- **AND** empty, whitespace-only and non-object JSON bodies retain the Python error mapping

#### Scenario: Cancellation and helper exit are isolated

- **WHEN** one usage query is cancelled before headers or while reading the body
- **THEN** its exchange is retired and another query can use the same helper
- **WHEN** the helper exits during a body read
- **THEN** that query fails without replay and a subsequent independent query can start a fresh helper
