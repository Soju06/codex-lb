## 1. Specs

- [x] 1.1 Tighten `responses-api-compat` with provider-aware route, transport, continuity, compact, and fail-closed error requirements.
- [x] 1.2 Tighten `sticky-session-operations` with provider-scoped mappings and rollout/backfill rules.
- [x] 1.3 Tighten `upstream-provider-management` with split persistence, route-family eligibility, and provider-aware list/detail contracts.
- [x] 1.4 Add provider-aware logging requirements to `proxy-runtime-observability`.
- [x] 1.5 Validate OpenSpec changes.

## 2. Persistence

- [x] 2.1 Introduce a separate OpenAI Platform upstream identity model instead of forcing API-key credentials into the existing ChatGPT `accounts` schema.
- [x] 2.2 Add a provider-aware routing-subject abstraction used by selection, sticky mappings, usage accounting, and request logging.
- [x] 2.3 Add provider scope and generic routing-subject references to sticky/session persistence and request-log persistence.
- [x] 2.4 Backfill existing ChatGPT-web rows and mappings with explicit provider scope and invalidate ambiguous legacy sticky mappings during rollout.
- [x] 2.5 Preserve current ChatGPT-web records and migrations without forced re-import.

## 3. Provider Adapters

- [x] 3.1 Introduce a provider adapter boundary for auth, model discovery, usage refresh, request execution, and capability checks.
- [x] 3.2 Keep the existing ChatGPT-web path behaviorally equivalent behind the adapter.
- [x] 3.3 Add an OpenAI Platform HTTP adapter for public `v1/*` endpoints.
- [x] 3.4 Specify and test wire-level Platform auth behavior: `Authorization: Bearer`, optional `OpenAI-Organization`, optional `OpenAI-Project`, and credential validation via a documented public API probe.
- [x] 3.5 Keep OpenAI Platform websocket mode out of scope in phase 1 unless a separate public websocket adapter is intentionally added and verified.

## 4. Routing and Proxy Behavior

- [x] 4.1 Add request-shape capability derivation before routing-subject selection.
- [x] 4.2 Make mixed-provider routing explicit with a fixed phase-1 enum of eligible route families, `chatgpt_web` primary selection, `/v1/models` plus stateless HTTP `/v1/responses` fallback scope, and Platform fallback driven by compatible ChatGPT pool health under the configured primary and secondary drain thresholds.
- [x] 4.3 Implement phase-1 Platform upstream support for HTTP `/v1/models` and stateless HTTP `/v1/responses`.
- [x] 4.4 Reject Platform-backed downstream websocket `/responses` and `/v1/responses` requests in phase 1 with stable OpenAI-format errors.
- [x] 4.5 Reject continuity-dependent Platform request shapes in phase 1, including `conversation`, `previous_response_id`, explicit session headers, and `x-codex-turn-state`.
- [x] 4.6 Restrict `/backend-api/codex/*` and both compact route families to `chatgpt_web` in phase 1.
- [x] 4.7 Freeze stable fail-closed error behavior for provider/route/transport/continuity capability mismatches, including HTTP status, OpenAI `type`, and `param` mapping per rejection path.

## 5. Dashboard and Operations

- [x] 5.1 Add provider-specific create/edit flows for ChatGPT-web accounts and Platform identities, including fallback-only operator copy, singleton/prerequisite guardrails, and route-scope messaging for `/v1/models` plus stateless HTTP `/v1/responses`.
- [x] 5.2 Add provider-aware list/detail responses including provider kind, routing-subject id, label, health, route-family eligibility, org/project metadata, last validation timestamp, and recent auth failure reason.
- [x] 5.3 Surface provider kind, routing-subject id, route class, upstream request id, and rejection reason in runtime logs and persisted request logs, keeping route-class logging separate from operator route-family eligibility enums.
- [x] 5.4 Document rollout and operator caveats for mixed-provider deployments.

## 6. Tests

- [x] 6.1 Add unit coverage for provider-aware persistence, routing-subject selection, and capability derivation.
- [x] 6.2 Add integration coverage for healthy-ChatGPT primary routing and Platform fallback when all compatible ChatGPT candidates are non-healthy under the configured primary or secondary drain thresholds for HTTP `/v1/models` and stateless HTTP `/v1/responses`.
- [x] 6.3 Add regression coverage proving ChatGPT-web `/backend-api/codex/*` and existing websocket behavior remain unchanged.
- [x] 6.4 Add explicit negative coverage proving Platform-backed `/backend-api/codex/*` is rejected in phase 1.
- [x] 6.5 Add explicit negative coverage proving Platform-backed downstream websocket `/responses` and `/v1/responses` are rejected in phase 1.
- [x] 6.6 Add explicit negative coverage proving Platform-backed compact routes are rejected in phase 1.
- [x] 6.7 Add explicit negative coverage proving continuity-dependent Platform request shapes fail closed, including `conversation` and `previous_response_id`.
- [x] 6.8 Add coverage proving rejections happen before upstream transport starts and assert the stable HTTP status / `type` / `code` / `param` contract for each fail-closed path.
- [x] 6.9 Add coverage proving provider-scoped sticky mappings do not leak across provider kinds.
