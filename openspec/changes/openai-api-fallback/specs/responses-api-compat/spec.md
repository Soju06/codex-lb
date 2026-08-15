# responses-api-compat Delta Specification

## ADDED Requirements

### Requirement: Operators can designate one Responses-capable Model Source as subscription fallback

The dashboard-managed Model Source configuration MUST allow an operator to opt in one enabled OpenAI-compatible source as the subscription fallback. A designated fallback source MUST support the Responses API. The operator MAY configure an enabled source model as a fallback model override; when no override is configured, the proxy MUST preserve the requested model. The system MUST reject a fallback configuration that is disabled, is not Responses-capable, or names a fallback model that is not enabled on that source. The persisted configuration MUST ensure that at most one Model Source is designated as the subscription fallback.

#### Scenario: Configure fallback in the existing Model Source dashboard

- **GIVEN** an enabled OpenAI-compatible Model Source with Responses support and an encrypted API key
- **WHEN** the operator enables subscription fallback for that source
- **THEN** the fallback designation is persisted with the Model Source
- **AND** no new host-level fallback credential environment variable is required
- **AND** any previously designated fallback source is no longer active as the fallback

#### Scenario: Configure a fallback model override

- **GIVEN** a designated fallback source exposes an enabled model named `external-coder`
- **WHEN** the operator configures `external-coder` as the fallback model override
- **THEN** overflow Responses requests use `external-coder` at that source

### Requirement: Responses overflow occurs only after aggregate subscription usage exhaustion

For HTTP Responses traffic that would normally use ChatGPT subscription accounts, the proxy MUST attempt the designated Model Source fallback only when normal account selection terminates with aggregate `usage_limit_reached`. The proxy MUST NOT use the fallback for local concurrency or fair-share limits, admission overload, authentication errors, unsupported models, transient upstream errors, arbitrary upstream HTTP 429 responses, or while another eligible subscription account can serve the request.

#### Scenario: All eligible subscription accounts are usage exhausted

- **GIVEN** a replay-safe Responses request and an eligible designated fallback Model Source
- **AND** every eligible subscription account is exhausted by upstream usage quota
- **WHEN** account selection returns aggregate `usage_limit_reached`
- **THEN** the proxy forwards the request to the designated fallback exactly once
- **AND** the client receives the Model Source result instead of the aggregate subscription quota error

#### Scenario: Local capacity does not trigger external fallback

- **GIVEN** a designated fallback Model Source
- **WHEN** subscription account selection fails because of a local account-capacity, fair-share, or admission limit
- **THEN** the proxy preserves the existing local-limit response
- **AND** does not contact the designated fallback source

### Requirement: Overflow fallback is fail-closed for account-owned continuity

Before attempting a Model Source fallback, the proxy MUST prove that the Responses payload is account-neutral and safe to replay to another provider. File-pinned requests, provider-owned conversation state, hosted-state references, and unverified retained-response state MUST NOT cross to the fallback source. A request with `previous_response_id` MAY cross only when the existing continuity proof can project it into a self-contained fresh replay with the retained upstream anchor removed.

#### Scenario: Fresh account-neutral request can overflow

- **GIVEN** a fresh Responses request with no account-owned file, conversation, prompt, or retained-response anchor
- **WHEN** aggregate subscription usage is exhausted
- **THEN** the request is eligible for fallback routing

#### Scenario: File-pinned request remains on the subscription failure path

- **GIVEN** a Responses request containing an account-owned input file reference
- **WHEN** aggregate subscription usage is exhausted
- **THEN** the proxy does not route the request to the Model Source fallback

#### Scenario: Verified retained response is projected to a fresh replay

- **GIVEN** a Responses request with `previous_response_id`
- **AND** the proxy has durable continuity evidence that the supplied input contains the complete replayable context
- **WHEN** aggregate subscription usage is exhausted
- **THEN** the proxy MAY route a self-contained replay to the fallback source
- **AND** the forwarded payload omits the account-owned `previous_response_id`

### Requirement: Fallback preserves source authorization and request accounting invariants

The fallback path MUST respect API-key Model Source assignment scope. The proxy MUST transfer the request's existing API-key usage reservation to Model Source forwarding rather than create a second reservation, and that reservation MUST be settled or released exactly once by the fallback path. A fallback provider error MUST be terminal for that request and MUST NOT recurse into subscription selection or another fallback attempt.

#### Scenario: Scoped API key cannot use an unassigned fallback source

- **GIVEN** an API key whose Model Source assignment scope excludes the designated fallback
- **WHEN** aggregate subscription usage is exhausted
- **THEN** the proxy preserves the subscription usage-limit response
- **AND** does not contact the excluded fallback source

#### Scenario: Existing reservation is reused by fallback

- **GIVEN** a limited API key request has already reserved request budget before subscription selection
- **WHEN** aggregate subscription usage is exhausted and fallback is eligible
- **THEN** Model Source forwarding receives that existing reservation
- **AND** no second admission reservation is created for the same request
- **AND** source forwarding settles or releases the reservation exactly once

#### Scenario: Fallback provider failure is terminal

- **GIVEN** aggregate subscription usage is exhausted and the designated fallback is attempted
- **WHEN** the fallback provider returns an error
- **THEN** the proxy returns the sanitized Model Source error
- **AND** does not retry the request against subscription accounts or another external provider
