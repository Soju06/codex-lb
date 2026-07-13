## Context

The `/v1/usage` endpoint currently returns `request_count`, `total_tokens`, `cached_input_tokens`, `total_cost_usd`, `limits[]` (API-key-level limits), and `upstream_limits[]` (aggregate Codex credit windows). There is no way for clients to see pooled account remaining capacity percentages.

The codebase already computes pooled credit data (`PooledCreditData` in `app/modules/api_keys/service.py:316-318`) using `_compute_pooled_credits()` — this is used for the dashboard API key list sidebar display. The same computation can be leveraged for `/v1/usage`.

We also want API key administrators to control which usage detail sections (`upstream_limits`, `account_pool_usage`) are visible to clients calling `/v1/usage`.

## Goals / Non-Goals

**Goals:**
- Add `account_pool_usage` object (`primary`, `secondary` float fields) to `GET /v1/usage` response
- Allow API key creators/editors to select which usage sections are exposed via a multi-select dropdown
- Store selection as `usage_sections` TEXT column with comma-separated values
- Default to all sections selected (`upstream_limits,account_pool_usage`)

**Non-Goals:**
- Changing existing `limits[]` or `upstream_limits[]` structure
- Adding new usage statistics beyond remaining percent
- Changing the public `/v1/models` endpoint

## Decisions

### Decision 1: Store `usage_sections` as comma-separated TEXT

**Rationale**: The existing codebase uses similar patterns (`allowed_models` as JSON array in TEXT). Comma-separated values are simpler than JSON for a fixed set of 2 options and don't require JSON parsing overhead. A migration sets the default to `"upstream_limits,account_pool_usage"`.

**Alternatives considered:**
- Two boolean columns (`show_upstream_limits`, `show_account_pool_usage`) — adds schema bloat for a small number of flags; the user explicitly requested TEXT with comma separator.
- JSON array — valid, but overkill for a fixed set of 2 values.

### Decision 2: Compute `account_pool_usage` in the `/v1/usage` handler

**Rationale**: The `/v1/usage` handler already has access to `ApiKeysService` and `UsageRepository`. It can reuse the existing `_compute_pooled_credits` helper or a similar computation path. This keeps the logic close to where it's consumed.

### Decision 3: Conditional response based on `usage_sections`

**Rationale**: The `/v1/usage` handler reads the API key's `usage_sections`, parses it into a set, and conditionally includes `upstream_limits` and `account_pool_usage` in the response. This is a simple filter pass — no complex orchestration needed.

## Risks / Trade-offs

- [Risk] Migration failure on large tables → Mitigation: adding a nullable TEXT column with a default value is a lightweight ALTER TABLE operation.
- [Risk] Out-of-sync `usage_sections` values if enum changes → Mitigation: the set of options (`upstream_limits`, `account_pool_usage`) is intentionally small and unlikely to change; parsing is tolerant of unknown values.
