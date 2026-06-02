## ADDED Requirements

### Requirement: Soft bridge affinity can reroute under local pressure

Prompt-cache and sticky-thread bridge affinity that does not carry a hard continuity dependency MUST be treated as soft. A client-supplied or proxy-derived `prompt_cache_key` is a cache-locality hint, not a correctness dependency; the proxy MAY reroute it under local pressure and accept lower cache-hit rates. When the preferred soft bridge session is saturated by queue depth, response-create gate pressure, bridge capacity, or account-local caps, the service MUST evaluate other eligible accounts/sessions before returning a local overload response. The service MUST emit internal diagnostics such as `internal_soft_affinity_reroute` for successful reroutes without adding those diagnostic names to the stable failure taxonomy.

#### Scenario: Prompt-cache bridge queue reroutes to an eligible account

- **GIVEN** a prompt-cache request's preferred bridge session queue is full
- **AND** another eligible account/session is below cap
- **WHEN** the request has no hard previous-response or turn-state continuity dependency
- **THEN** the proxy routes to the alternate account/session
- **AND** records an internal soft-affinity reroute diagnostic

#### Scenario: Prompt cache key does not override hard previous-response continuity

- **GIVEN** a `/v1/responses` request carries both `previous_response_id` and `prompt_cache_key`
- **AND** the previous response owner is known
- **WHEN** the prompt-cache preferred account differs from the previous-response owner
- **THEN** the proxy treats the request as hard owner-bound to the previous-response owner
- **AND** it does not route to the prompt-cache account when that account cannot preserve the stored response continuation

### Requirement: Hard continuity remains owner-bound and bounded

Requests that depend on `previous_response_id`, hard turn-state, account-scoped `input_file.file_id` pins, or another required owner continuity source MUST NOT silently reroute to an account that cannot preserve continuity. A `previous_response_id` is a stored-object continuation reference and remains owner-bound even when the same request also carries `prompt_cache_key` or another soft locality key. If the owner account/session is unavailable or saturated, the service MUST fail closed with an explicit retryable continuity/local overload reason instead of flooding the owner queue indefinitely.

#### Scenario: Previous-response owner queue is saturated

- **WHEN** a `/v1/responses` follow-up requires a previous-response owner
- **AND** the owner session queue or account cap is saturated
- **THEN** the service fails closed with `hard_affinity_saturated` or `previous_response_owner_unavailable`
- **AND** it does not route to an unrelated account that lacks continuity state

#### Scenario: File-pinned request owner is capped

- **WHEN** a `/v1/responses` request references an `input_file.file_id` pinned to an owner account
- **AND** the owner account is at its account stream or response-create cap
- **THEN** the service returns a local account-cap overload for the owner
- **AND** it does not route the file reference to another account
