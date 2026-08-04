## Context

Live Voice has two authenticated legs: HTTP call creation and a control sideband WebSocket. The serving upstream account must remain identical across both legs. Official Codex supplies one ChatGPT OAuth bearer plus `chatgpt-account-id`; Key-based clients supply a registered Codex-LB Proxy API Key.

## Goals

- Preserve `model_provider = "openai"` and official OAuth.
- Preserve the existing registered-Key Codex profile and all Key authorization semantics.
- Support OAuth callers that have no corresponding imported Account row.
- Apply one operator-managed upstream pool to every verified OAuth caller.
- Preserve Key behavior and exact affinity input.
- Keep ownership immutable, private, bounded, and fail closed.

## Decisions

### Classify bearer before authorization

`sk-clb-` bearers use strict Proxy Key validation. Other bearers require `chatgpt-account-id` and enter OAuth verification. Both call-create and all three sideband routes share this resolver.

### Separate caller identity from serving accounts

The OAuth resolver validates the supplied credential pair against the upstream usage endpoint. Every caller with a stable verified `chatgpt_user_id` or `sub` receives `principal:{stable_seat_claim}` whether or not an imported Account matches. A matching imported Account remains optional `caller_account_id` metadata for usage and route integration. Credentials without a stable claim fall back to one unambiguous imported Account id. External callers follow the configured default upstream proxy route when routing is enabled.

The typed result contains `principal_id`, optional `caller_account_id` for usage integration, normalized ChatGPT account id, verified usage payload, and route. Raw credentials remain absent from logs and persistence. OAuth Live accepts independent verified principals through its global policy, while `/api/codex/usage` requires an eligible imported `caller_account_id` before exposing aggregate local pool usage.

Identity validation admits at most 32 distinct in-flight credential pairs per process. Same-pair callers share one task. The final departing waiter cancels unfinished work, and cancelling work retains its admission slot until the task drains.

### Use one global policy

`oauth_live_global_policy` contains singleton id `1`, active state, and timestamps. `oauth_live_global_policy_accounts` links it to imported serving Accounts. Dashboard writes replace the complete set transactionally. Active policy writes require a non-empty known set; runtime lookup filters to currently active Accounts.

All verified OAuth principals share this pool. Each principal still receives isolated affinity material, so one principal cannot attach another principal's call after learning its call id.

### Keep Key and OAuth lanes compatible

Key callers keep `api_key.id` affinity, assignments, limits, reservations, last-used updates, and request-log attribution. Their Codex profile continues to use `requires_openai_auth = true` for app-visible ChatGPT capabilities and `env_key` for the registered Codex-LB bearer. OAuth callers use `oauth:{principal_id}`, select within the global pool, and write request logs with `api_key_id = NULL`.

### Keep the Settings job singular

The Live Voice card answers one operator question: which upstream Accounts may serve verified OAuth Live calls? It exposes one global enable switch, one explicit account multi-select, and one save action. Caller inputs and caller-to-account matching are absent from the UI. The compact selector keeps selected unavailable Accounts visible so operators can identify and remove stale assignments while other unavailable Accounts remain excluded.

## Migration and rollback

One revision creates the global singleton policy table and its allowed-account relationship table. The initial API state is disabled with an empty pool. Downgrade removes the relationship table before the singleton table.

## Risks

- OAuth validation adds an upstream usage call on cache miss; bounded caching and singleflight limit fan-out.
- External principals require a stable verified seat claim, preserving cross-refresh ownership.
- Experimental client route keys can drift, so each bundled Codex upgrade requires call-create, sideband, and audible Live E2E acceptance.
- Client profiles can appear healthy while only one product path works, so acceptance covers normal conversation and audible Live Voice for both OAuth and registered-Key modes.
