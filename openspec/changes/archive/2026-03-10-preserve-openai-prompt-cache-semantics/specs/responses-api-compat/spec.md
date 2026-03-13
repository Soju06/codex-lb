## MODIFIED Requirements

### Requirement: Strip known unsupported advisory parameters before upstream forwarding
Before forwarding Responses payloads upstream, the service MUST remove known unsupported advisory parameters that upstream rejects with `unknown_parameter`. At minimum, the service MUST strip `temperature` and `prompt_cache_retention` from normalized payloads for both standard and compact Responses endpoints, and MUST preserve `prompt_cache_key`.

#### Scenario: prompt_cache_retention provided
- **WHEN** a client sends a valid Responses request that includes `prompt_cache_retention`
- **THEN** the service accepts the request and forwards payload without `prompt_cache_retention`

#### Scenario: temperature provided
- **WHEN** a client sends a valid Responses or Chat-mapped request that includes `temperature`
- **THEN** the service accepts the request and forwards payload without `temperature`

#### Scenario: unrelated extra field provided
- **WHEN** a client sends a valid request with an unrelated extra field not in the unsupported list
- **THEN** the service preserves that field in forwarded payload

## ADDED Requirements

### Requirement: Use prompt_cache_key as OpenAI cache affinity
For OpenAI-style `/v1/responses` and `/v1/responses/compact` requests, the service MUST treat a non-empty `prompt_cache_key` as the upstream account affinity key for prompt-cache correctness. This affinity MUST apply even when dashboard `sticky_threads_enabled` is disabled, and the service MUST continue forwarding the same `prompt_cache_key` upstream unchanged.

#### Scenario: /v1 responses request pins account with prompt_cache_key
- **WHEN** a client sends repeated `/v1/responses` requests with the same non-empty `prompt_cache_key` while `sticky_threads_enabled` is disabled
- **THEN** the service selects the same upstream account for those requests

#### Scenario: /v1 compact request reuses prompt-cache affinity
- **WHEN** a client sends `/v1/responses/compact` after `/v1/responses` with the same non-empty `prompt_cache_key` while `sticky_threads_enabled` is disabled
- **THEN** the compact request reuses the previously selected upstream account

### Requirement: Normalize prompt cache aliases for upstream compatibility
Before forwarding Responses payloads upstream, the service MUST normalize OpenAI-compatible camelCase prompt cache controls so codex-lb applies compatibility behavior consistently. The service MUST forward `promptCacheKey` as `prompt_cache_key`, and MUST treat `promptCacheRetention` the same as `prompt_cache_retention` for stripping behavior.

#### Scenario: camelCase prompt cache fields provided
- **WHEN** a client sends `promptCacheKey` or `promptCacheRetention` on a valid Responses request
- **THEN** the service forwards `prompt_cache_key` with the same value and does not forward `prompt_cache_retention`
