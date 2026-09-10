## MODIFIED Requirements

### Requirement: Upstream Responses event size budget
The service SHALL allow upstream Responses SSE events and upstream websocket message frames up to 16 MiB before treating them as oversized. The budget is a fixed application constant (`MAX_SSE_EVENT_BYTES` in `app/core/clients/proxy.py`); it MUST NOT be operator-configurable, and the serialized upstream `response.create` budget (15 MiB) MUST be derived from it so the envelope can never exceed the frame ceiling.

#### Scenario: built-in tool output exceeds the old 2 MiB limit
- **WHEN** upstream Responses traffic includes a single SSE event or websocket message frame larger than 2 MiB but not larger than 16 MiB
- **THEN** the proxy continues processing the event instead of closing the upstream websocket locally with `1009 message too big`

#### Scenario: The event budget is not an environment setting
- **WHEN** the process starts with `CODEX_LB_MAX_SSE_EVENT_BYTES` or `CODEX_LB_UPSTREAM_RESPONSE_CREATE_MAX_BYTES` set
- **THEN** the values are ignored, startup logs the removed-setting warning once, and the 16 MiB / 15 MiB budgets apply

### Requirement: Codex compact requests are bounded by the proxy request budget
When `/backend-api/codex/responses/compact` is called for Codex auto-compaction, the service MUST bound the upstream compact call by the remaining proxy compact request budget. That budget (the dashboard `compact_request_budget_seconds`) is the only total cap on the upstream compact call; there is no separate upstream compact timeout setting. The service MUST preserve Codex turn metadata `request_kind` in compact request logs so auto-compaction failures are distinguishable from normal user turns.

#### Scenario: auto-compaction cannot hang past the proxy budget
- **GIVEN** a Codex compact request carries `x-codex-turn-metadata` with `request_kind: "compaction"`
- **WHEN** the service calls upstream
- **THEN** the upstream call receives both connect and total timeout overrides from the remaining compact request budget
- **AND** no other total timeout is applied to the upstream compact call
- **AND** the request log records `request_kind` as `compaction`

### Requirement: Responses HTTP ingress uses the expanded bounded budget

HTTP requests to `/v1/responses` and `/backend-api/codex/responses`, including trailing-slash variants, MUST use the larger of the general HTTP body budget (`MAX_DECOMPRESSED_BODY_BYTES`, 32 MiB) and the Responses body budget (`MAX_DECOMPRESSED_RESPONSES_BODY_BYTES`, 128 MiB) as both the raw-body and decompressed-body ingress budget. Both budgets are fixed application constants in `app/core/ingress_limits.py` and MUST NOT be operator-configurable; the Responses budget MUST remain 128 MiB and MUST be the same constant that seeds the downstream websocket `--ws-max-size` default.

The trailing-slash variants MUST be hidden aliases of the canonical HTTP handlers rather than redirects, so streamed bodies receive the same admission, authorization, and route behavior.

If either representation exceeds that budget, the service MUST stop before route logic or upstream forwarding and return HTTP 413 with an OpenAI-compatible error envelope carrying `error.code = payload_too_large` and `error.type = invalid_request_error`.

This transport-ingress 413 applies before parsing and is distinct from the existing application-level oversized-`response.create` guard. A request that fits the 128 MiB transport budget but still exceeds the upstream websocket budget after historical slimming MUST retain the existing HTTP 400 `payload_too_large` behavior and `param = input`.

#### Scenario: Larger Responses request fits both ingress checks

- **WHEN** a Responses HTTP request is larger than the general budget but no larger than the Responses budget in either raw or decompressed form
- **THEN** the ingress guards allow the request to continue to Responses route handling

#### Scenario: Trailing-slash Responses request is admitted without redirect

- **WHEN** a client sends a chunked HTTP request to `/v1/responses/` or `/backend-api/codex/responses/`
- **THEN** the service applies the same ingress budget and handler as the corresponding canonical path
- **AND** it does not return a trailing-slash redirect before consuming the guarded body

#### Scenario: Responses raw body exceeds its budget

- **WHEN** a Responses HTTP request's raw body exceeds the Responses budget
- **THEN** the service returns HTTP 413 with `error.code = payload_too_large` and `error.type = invalid_request_error`
- **AND** the service does not invoke Responses route logic or forward the request upstream

#### Scenario: Responses expanded body exceeds its budget

- **WHEN** an encoded Responses HTTP request fits the raw budget but expands beyond the Responses budget
- **THEN** the service returns HTTP 413 with `error.code = payload_too_large` and `error.type = invalid_request_error`
- **AND** the service does not invoke Responses route logic or forward the request upstream

#### Scenario: Post-slimming application rejection remains 400

- **WHEN** a Responses HTTP request fits the raw and decompressed transport-ingress budget
- **AND** its serialized `response.create` still exceeds the upstream websocket budget after historical slimming
- **THEN** the existing application-level guard returns HTTP 400 with `error.code = payload_too_large`, `error.type = invalid_request_error`, and `error.param = input`

## REMOVED Requirements

### Requirement: Proxy-generated prompt cache key derivation is operator-toggleable

**Reason**: The derivation flag (`CODEX_LB_OPENAI_PROMPT_CACHE_KEY_DERIVATION_ENABLED`) was never set by any deployment; proxy-generated prompt-cache-key derivation is now always on when OpenAI cache affinity applies, and a client-supplied `prompt_cache_key` is still forwarded unchanged (covered by "Use prompt_cache_key as OpenAI cache affinity" and "Backend Codex request derives prompt_cache_key before codex-session routing").

**Migration**: Remove the variable from the environment and the Helm value `config.promptCacheKeyDerivationEnabled`; startup warns once while the name is still set.
