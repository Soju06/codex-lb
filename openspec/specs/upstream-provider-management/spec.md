# upstream-provider-management Specification

## Purpose

Define provider-aware upstream identity management, credential lifecycle rules, and mixed-provider routing eligibility.

## Requirements
### Requirement: Dashboard manages provider-specific upstream identities
The system SHALL allow operators to manage upstream identities for at least two provider kinds: `chatgpt_web` and `openai_platform`. Each identity MUST declare its provider kind explicitly, and the dashboard MUST present provider-specific create and edit flows instead of forcing all upstream credentials through the ChatGPT OAuth path.

#### Scenario: Operator creates an OpenAI Platform upstream identity
- **WHEN** the operator creates an `openai_platform` upstream identity
- **THEN** the dashboard collects a human label, encrypted API key material, optional organization or project metadata, and route-family eligibility settings drawn from the fixed phase-1 enum
- **AND** the system stores that identity without requiring ChatGPT OAuth tokens, `refresh_token`, `id_token`, or `chatgpt_account_id`

#### Scenario: Platform identity requires an existing ChatGPT-web pool
- **WHEN** the operator attempts to create an `openai_platform` upstream identity
- **AND** there is no active `chatgpt_web` account available to serve the existing primary path
- **THEN** the system rejects the create request
- **AND** it explains that Platform fallback requires at least one active ChatGPT-web account

#### Scenario: Only one Platform identity may exist
- **WHEN** the operator attempts to create a second `openai_platform` upstream identity
- **THEN** the system rejects the create request
- **AND** it explains that phase-1 mixed-provider mode supports only one Platform API key

#### Scenario: Operator creates a ChatGPT-web upstream identity
- **WHEN** the operator creates a `chatgpt_web` upstream identity
- **THEN** the existing OAuth or `auth.json`-import flow remains available
- **AND** the system continues storing the ChatGPT-specific credential set required for that provider

### Requirement: Platform identities use split persistence, not fake ChatGPT account fields
The system MUST store `openai_platform` credentials in a provider-appropriate persistence model and MUST NOT require fake ChatGPT account fields such as refresh tokens, `id_token`, or `chatgpt_account_id` to represent a valid Platform identity.

#### Scenario: Platform identity persists without ChatGPT account fields
- **WHEN** the system persists an `openai_platform` upstream identity
- **THEN** it stores provider-appropriate credential and metadata fields only
- **AND** it does not depend on nullable fake ChatGPT lifecycle fields to keep the record valid

### Requirement: Provider credentials follow provider-specific lifecycle rules
The system MUST apply credential lifecycle behavior according to provider kind. `chatgpt_web` identities continue to use token refresh and account-claim extraction. `openai_platform` identities MUST NOT enter the ChatGPT OAuth refresh lifecycle and MUST instead use provider-appropriate key validation and health transitions.

#### Scenario: Platform upstream request uses bearer auth and optional org/project headers
- **WHEN** the system sends an upstream request through an `openai_platform` identity
- **THEN** it sends `Authorization: Bearer <api_key>`
- **AND** it sends `OpenAI-Organization` only when the identity configures organization metadata
- **AND** it sends `OpenAI-Project` only when the identity configures project metadata

#### Scenario: Platform upstream identity validates without refresh tokens
- **WHEN** the system validates an `openai_platform` upstream identity
- **THEN** it performs API-key validation with `GET /v1/models` using the same auth headers as normal Platform requests
- **AND** a `2xx` response marks validation success
- **AND** repeated `401` or `403` responses are treated as credential failure
- **AND** it MUST NOT attempt to call the ChatGPT OAuth refresh path for that identity

#### Scenario: Platform upstream identity fails closed after repeated upstream auth failures
- **WHEN** an `openai_platform` upstream identity repeatedly receives upstream `401` or `403` authentication failures
- **THEN** the system marks that identity unhealthy or deactivated according to provider-specific policy
- **AND** it stops selecting that identity for new requests until the operator repairs or re-enables it

### Requirement: Mixed-provider routing policy is explicit
The system MUST expose an explicit route-family eligibility policy for each upstream identity. In phase 1, `openai_platform` identities are opt-in for a fixed enum of eligible public HTTP route families, are fallback-only behind the existing ChatGPT pool, and are not silently merged into ChatGPT-private pools.

#### Scenario: Platform identity is disabled for public HTTP routes by default
- **WHEN** an operator adds an `openai_platform` upstream identity
- **AND** the operator has not enabled any public HTTP route families for that identity
- **THEN** the router excludes that identity from request selection

#### Scenario: Platform identity is eligible only for enabled route families
- **WHEN** an operator enables a subset of public HTTP route families for an `openai_platform` identity
- **THEN** the router considers that identity only for those route families
- **AND** it continues excluding the identity from ChatGPT-private routes, compact routes, websocket routes, and continuity-dependent phase-1 behavior

#### Scenario: Healthy ChatGPT-web pool stays primary for supported public routes
- **WHEN** a request targets an eligible public HTTP route
- **AND** both `chatgpt_web` and `openai_platform` identities are configured for that route family
- **AND** at least one compatible ChatGPT-web account remains healthy under the configured primary and secondary drain thresholds
- **THEN** the service keeps routing through the ChatGPT-web pool
- **AND** it does not select the Platform identity for that request

#### Scenario: Platform becomes fallback when the compatible ChatGPT pool has no healthy candidates
- **WHEN** a request targets an eligible public HTTP route
- **AND** both `chatgpt_web` and `openai_platform` identities are configured for that route family
- **AND** all compatible ChatGPT-web candidates are at or above either configured drain threshold
- **THEN** the service MAY select the Platform identity as fallback for that request
- **AND** it does so only for `/v1/models` and stateless HTTP `/v1/responses`

#### Scenario: Phase-1 route-family enum is fixed and testable
- **WHEN** the system exposes route-family eligibility controls for `openai_platform`
- **THEN** the supported phase-1 enum values are fixed and testable
- **AND** they include only `public_models_http` and `public_responses_http`

### Requirement: Provider capabilities gate route eligibility
Each upstream identity MUST expose or derive a provider capability set that the router and balancer use before selection. The service MUST filter by provider capability before it chooses the concrete upstream identity for a request.

#### Scenario: Platform identity is excluded from ChatGPT-private route selection
- **WHEN** a request requires a ChatGPT-private capability such as any `/backend-api/codex/*` route, provider-owned compact semantics, or downstream websocket transport
- **AND** the candidate upstream identity is `openai_platform`
- **THEN** the selection process excludes that identity before the normal routing strategy runs

#### Scenario: Public HTTP route can fall back to Platform only after the compatible ChatGPT pool is drained
- **WHEN** a request targets an eligible public HTTP route such as `/v1/responses`
- **AND** both `chatgpt_web` and `openai_platform` identities advertise support for the request
- **AND** both are enabled for that route family by policy
- **AND** no compatible ChatGPT-web candidate remains healthy under the configured primary and secondary drain thresholds
- **THEN** the service may route the request to the Platform identity as fallback
- **AND** it MUST NOT treat Platform as an equal-weight member of the normal ChatGPT routing pool

### Requirement: Provider list and detail surfaces expose operational state
The system MUST expose provider-aware list/detail fields so operators can understand why an upstream identity is or is not eligible for a request.

#### Scenario: Provider summary includes route eligibility and health
- **WHEN** the dashboard or API returns a list of upstream identities
- **THEN** each item includes `provider_kind`, `routing_subject_id`, operator-visible label, health/status, eligible route families, and last validation timestamp

#### Scenario: Provider detail includes recent auth failure reason
- **WHEN** the dashboard or API returns a detail view for an upstream identity
- **THEN** the response includes the most recent provider-auth failure code or reason when available
- **AND** it includes configured organization and project metadata when present
