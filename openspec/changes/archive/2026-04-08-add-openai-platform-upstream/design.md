## Context

The current architecture has one upstream contract:

- identity model: ChatGPT account
- auth model: `access_token` + `refresh_token` + `id_token`
- routing hint: `chatgpt-account-id`
- transport target: `chatgpt.com/backend-api`

That contract leaks into persistence, selection, refresh, model discovery, usage refresh, sticky-session handling, request logging, and dashboard UX. At the same time, OpenAI's current Codex and Responses documentation support API-key-backed access through the public API, which uses a different upstream contract:

- identity model: API key / project / org
- auth model: long-lived API key, no refresh flow
- routing hint: no `chatgpt-account-id`
- transport target: public `v1/*` endpoints

The design goal is not to replace the ChatGPT-web path. It is to add an OpenAI Platform path without regressing current pooled ChatGPT behavior.

## Goals / Non-Goals

**Goals:**

- Support two upstream provider kinds: `chatgpt_web` and `openai_platform`.
- Preserve existing ChatGPT-web behavior for `/backend-api/codex/*` and current compact/session semantics.
- Support phase-1 public OpenAI-compatible HTTP routes through `chatgpt_web` first, with `openai_platform` eligible only as explicit fallback when the compatible ChatGPT pool has no healthy candidates left under the configured primary and secondary drain thresholds and the request stays within the supported public feature set.
- Keep account selection, API-key enforcement, logging, sticky mappings, and dashboard management coherent across provider kinds.
- Fail closed when a request asks for behavior that the selected provider kind cannot satisfy.

**Non-Goals:**

- Replace the existing ChatGPT-web path.
- Force OpenAI Platform credentials into the current ChatGPT OAuth-shaped `accounts` table.
- Guarantee that every ChatGPT-private Codex behavior has a public API equivalent.
- Implement public Responses websocket mode, compact parity, provider-owned continuity, or Platform-backed `/v1/chat/completions` in phase 1.
- Change downstream route shapes in the first phase.

## Decisions

### Split ChatGPT accounts and Platform identities at persistence time

Phase 1 will not reuse the current `accounts` schema for `openai_platform`. The current schema and lifecycle are ChatGPT-specific: email, plan type, refresh tokens, `id_token`, `last_refresh`, and `chatgpt_account_id` are part of the stored contract.

Phase-1 persistence shape:

- keep `accounts` unchanged for `chatgpt_web`
- add a new provider-managed upstream identity record for `openai_platform`
- introduce a provider-aware routing subject abstraction that normalizes:
  - `provider_kind`
  - `routing_subject_id`
  - status / health
  - operator-visible label
  - eligible route families
  - capability flags

The balancer, sticky-session layer, and request logger should target that routing-subject abstraction instead of assuming every upstream target is an `Account`.

Alternative considered: add nullable provider columns directly to `accounts`.
Rejected because the current table is materially ChatGPT-OAuth-shaped and would either force fake Platform data or require a large nullability refactor before provider-aware behavior exists.

### Split provider behavior behind an adapter boundary

Create a provider adapter interface that owns the pieces that differ by upstream:

- request auth/header construction
- base URL resolution
- token refresh or API-key validation
- model discovery
- usage refresh / accounting inputs
- route and transport capability checks
- request normalization quirks

Recommended adapter families:

- `ChatGPTWebProviderAdapter`
- `OpenAIPlatformProviderAdapter`

`ProxyService`, model refresh, and usage refresh should depend on this interface rather than branching on provider kind throughout the call stack.

For `openai_platform`, the adapter contract must be wire-level explicit:

- send `Authorization: Bearer <api_key>` on upstream requests
- send `OpenAI-Organization` only when operator metadata configures an organization
- send `OpenAI-Project` only when operator metadata configures a project
- validate credentials using the same auth headers against `GET /v1/models`
- treat `2xx` as validation success and repeated `401` or `403` as credential failure

### Make mixed-provider routing an explicit fixed route-family opt-in policy

Phase 1 will not place ChatGPT-web accounts and Platform identities into one implicit global weighted pool.

Routing policy:

1. Determine the requested route family and transport.
2. Derive the required capability set from route, request shape, and headers.
3. Select from the existing `chatgpt_web` pool first using current routing strategy and usage accounting.
4. For public HTTP route families only, allow `openai_platform` as fallback when no compatible ChatGPT candidate remains healthy under the configured primary and secondary drain thresholds.
5. Filter routing subjects to those whose provider kind and eligibility policy allow that capability set, then execute against the chosen provider contract.

Phase-1 policy defaults:

- `chatgpt_web` remains eligible for current route families by default
- `chatgpt_web` remains the primary provider for all routes it already serves today
- `openai_platform` identities are opt-in only for these fixed phase-1 route families:
  - `public_models_http`
  - `public_responses_http`
- `openai_platform` identities are not eligible for ChatGPT-private routes, compact routes, or downstream websocket routes in phase 1
- `openai_platform` identities are fallback-only for supported public routes and MUST NOT be used when there is no compatible ChatGPT-web account pool
- `openai_platform` fallback is triggered only when every compatible ChatGPT-web candidate is at or above either configured drain threshold
- only one `openai_platform` identity may be registered at a time

This keeps rollout predictable in mixed-provider deployments and avoids silently changing current ChatGPT-web routing behavior.

### Use an explicit `codex-lb` phase-1 support matrix and pre-routing guards

Provider compatibility must be explicit and must be enforced before transport start.

The matrix below describes what `codex-lb` phase 1 chooses to support, not the full upstream capability of the OpenAI Platform API.

Phase-1 `codex-lb` support matrix:

| Capability | chatgpt_web | openai_platform |
| --- | --- | --- |
| HTTP `/v1/models` | yes | yes |
| HTTP `/v1/responses` | yes | yes for stateless requests |
| HTTP `/v1/chat/completions` | yes | no in phase 1 |
| Downstream websocket `/responses` | yes | no |
| Downstream websocket `/v1/responses` | yes | no |
| `/backend-api/codex/*` | yes | no |
| `/v1/responses/compact` | yes | no |
| `/backend-api/codex/responses/compact` | yes | no |
| ChatGPT-owned HTTP bridge continuity | yes | no |
| `previous_response_id` continuity handling | yes | no in phase 1 |
| `session_id` / `x-codex-session-id` / `x-codex-conversation-id` / `x-codex-turn-state` affinity | yes | no in phase 1 |

Requests are continuity-dependent in phase 1 when they rely on any provider-owned or proxy-owned continuity hint, including:

- `conversation`
- `previous_response_id`
- `session_id`
- `x-codex-session-id`
- `x-codex-conversation-id`
- `x-codex-turn-state`
- downstream websocket upgrade

If the candidate provider set cannot satisfy the required capability set, the proxy must fail closed before selection or transport start with a stable OpenAI-format error.

### Treat public HTTP routes, websocket routes, compact routes, and ChatGPT-private routes differently

Phase 1 is intentionally conservative:

- public HTTP `/v1/models` and stateless HTTP `/v1/responses` may fall back to `openai_platform`
- downstream websocket `/responses` and `/v1/responses` remain `chatgpt_web`-only
- `/backend-api/codex/*` remains `chatgpt_web`-only
- `/v1/responses/compact` and `/backend-api/codex/responses/compact` remain `chatgpt_web`-only
- Platform-backed `/v1/chat/completions` remains out of scope in phase 1 until a supported mapped subset is specified separately

OpenAI Platform websocket support is a later phase even though the public API documents it. The current local websocket implementation is ChatGPT-private shaped and cannot be widened safely without a separate public websocket adapter and explicit protocol verification.

### Refresh and validation lifecycles diverge by provider

`chatgpt_web` continues using refresh-token based rotation and `id_token` claim extraction.

`openai_platform` must not imitate that lifecycle. Instead:

- validate API keys with a cheap public API probe on create/update
- cache successful verification state
- mark the identity unhealthy after repeated `401`/`403`
- never run refresh-token flows for Platform identities

Shared health concepts such as `active`, `paused`, and `deactivated` remain valid, but the transition reasons differ by provider kind.

### Dashboard UX adds provider-specific create and list/detail contracts

Keep the current Add Account OAuth/import experience for ChatGPT-web identities.

Add a separate create/edit flow for Platform identities with:

- provider kind selector
- human label
- API key input
- optional org/project fields
- route-family eligibility settings
- validation action
- a note that Platform is fallback-only for `/v1/models` and stateless HTTP `/v1/responses`
- a guard that requires at least one active `chatgpt_web` account before Platform registration
- a guard that only one Platform API key may exist at a time
- capability notes explaining that ChatGPT-private routes, compact, websocket, and continuity-dependent requests are unavailable in phase 1

Provider list/detail responses must expose at minimum:

- `provider_kind`
- routing subject id
- operator label
- health / status
- eligible route families
- configured organization and project metadata when present
- last validation timestamp
- most recent provider-auth failure code or reason

### Preserve observability with provider dimensions

Add provider-aware dimensions to request logs, health transitions, and runtime request summaries.

At minimum record:

- selected provider kind
- selected routing subject id or label
- upstream route class (`chatgpt_private`, `openai_public_http`, `openai_public_ws`) kept separate from operator route-family eligibility enums
- upstream OpenAI request id when present
- capability mismatch failures
- pre-routing rejection reason when no routing subject is selected

This is necessary because phase 1 intentionally introduces requests that may fail before account selection.

### Sticky-session migration must be provider-scoped

Sticky/session state today is effectively ChatGPT-account-centric. Phase 1 must make the namespace provider-aware.

Required changes:

- include provider scope in sticky/session persistence and lookup
- backfill existing sticky mappings as `chatgpt_web`
- invalidate or refuse ambiguous legacy mappings during rollout instead of reusing them across provider kinds
- keep `codex_session` continuity isolated to `chatgpt_web`
- allow provider-scoped prompt-cache affinity for Platform mode only where no continuity-dependent behavior is implied

Low-level implementation rules:

- treat the durable sticky mapping identity as `(provider_kind, kind, key)` instead of `(kind, key)`
- treat `(provider_kind, routing_subject_id)` as the source-of-truth sticky target and keep `account_id` only as a ChatGPT-web convenience field
- require persisted `routing_subject_id` to be non-empty after rollout; do not silently backfill new rows to `chatgpt_web` when the caller omits provider metadata
- reject or refuse to persist `openai_platform` rows for `codex_session`
- scope lookup, upsert, delete, cleanup, and list operations by provider identity so one provider cannot overwrite or delete another provider's identical sticky key

Recommended sticky persistence shape after the provider-scoped cutover:

| Column | Notes |
| --- | --- |
| `provider_kind` | required; one of `chatgpt_web`, `openai_platform` |
| `kind` | required sticky mapping kind |
| `key` | required sticky key within the provider-scoped namespace |
| `routing_subject_id` | required generic upstream identity key |
| `account_id` | nullable ChatGPT-web convenience column only |
| `created_at`, `updated_at` | unchanged timestamps |

Recommended database constraints and indexes:

- primary key or equivalent uniqueness on `(provider_kind, kind, key)`
- `routing_subject_id <> ''`
- `account_id IS NULL OR provider_kind = 'chatgpt_web'`
- `NOT (provider_kind = 'openai_platform' AND kind = 'codex_session')`
- index on `(provider_kind, routing_subject_id, kind)`
- existing stale-cleanup index on `(kind, updated_at)` retained

Rollout and migration rules:

- add a forward-only follow-up migration that rebuilds `sticky_sessions` into the provider-scoped key shape; do not edit an already-applied migration revision
- backfill legacy rows as `provider_kind='chatgpt_web'` and `routing_subject_id=account_id`
- when a legacy row cannot deterministically resolve to one provider-scoped routing subject, drop or invalidate it instead of attempting cross-provider reuse
- until the new provider-scoped sticky schema and repository contract are both active, keep Platform sticky persistence feature-gated off so Platform requests behave as sticky misses rather than unsafe writes

Repository and runtime boundary changes:

- introduce a small routing-target value object containing `provider_kind`, `routing_subject_id`, and optional `account_id`
- add provider-aware sticky repository methods that read and write routing targets directly
- keep compatibility wrappers for ChatGPT-web account-centric callers until the balancer boundary is fully moved to routing subjects
- move provider-specific candidate filtering above the existing ChatGPT load balancer so the first generic step is “choose the compatible routing-subject cohort”, not “make the balancer understand every provider at once”
- keep the existing ChatGPT balancer behavior inside the ChatGPT cohort until provider-scoped sticky persistence and capability gating are both proven by regression coverage

Operator-facing read-model implications:

- sticky-session list entries should expose `provider_kind`, `routing_subject_id`, and a provider-resolved label instead of assuming every row resolves through `accounts.email`
- delete and bulk-delete flows must include provider scope in the identifier contract
- rollout diagnostics should make it visible when a legacy row was backfilled, ignored as stale, or invalidated as ambiguous

## Risks / Trade-offs

- [Risk] Introducing a second persistence model adds short-term indirection.  
  Mitigation: keep the abstraction at the routing-subject layer and defer deep storage unification.

- [Risk] Public API behavior will not match all ChatGPT-private Codex semantics.  
  Mitigation: phase provider support by route family and fail closed for unsupported combinations.

- [Risk] Existing sticky-session and HTTP bridge assumptions are ChatGPT-shaped.  
  Mitigation: keep those features ChatGPT-only in phase 1 and explicitly reject continuity-dependent Platform requests.

- [Risk] Dashboard complexity increases.  
  Mitigation: use provider-specific create forms and explicit route-family eligibility settings instead of one overloaded account form.

## Migration Plan

### Phase 1: Persistence and provider scaffolding

- add a new Platform upstream identity model and storage
- add provider-aware routing-subject repositories
- add provider scope to sticky/session mappings and request logs
- backfill existing ChatGPT-web rows and mappings with explicit provider scope
- add provider adapter interface with ChatGPT-web implementation kept behaviorally identical

### Phase 2: Public HTTP API routing

- enable `openai_platform` candidates on HTTP `/v1/models` and stateless HTTP `/v1/responses`
- add request-shape capability gating before selection
- add provider-aware model discovery and request execution
- keep websocket, compact, `conversation`, `previous_response_id`, and other continuity-dependent request shapes fail-closed for Platform mode

### Phase 3: Dashboard and operations

- add provider-specific create/edit flows
- expose provider list/detail contracts with health and eligibility fields
- add provider-aware observability dimensions and operator guidance

### Phase 4: Follow-up evaluation

- decide whether native public websocket mode is viable for Platform-backed traffic
- decide whether any `/backend-api/codex/*` compatibility layer is safe
- decide whether public compact and provider-owned continuity support are mature enough to widen capability coverage
- decide whether Platform-backed `/v1/chat/completions` can be safely specified as an explicit mapped subset

## Open Questions

- Should local proxy API keys be able to constrain allowed provider kinds, not just allowed routing subjects and models?
- Should request-log storage keep both legacy `account_id` and a new generic `routing_subject_id`, or migrate fully to the generic id in one step?
