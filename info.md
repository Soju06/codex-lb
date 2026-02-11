# Main Bug Review - `e210a4c`

## Decisions from user

- Dashboard must remain open when TOTP enforcement is disabled.
- `/api/codex/usage` is intentionally outside firewall scope.
- Spec point #3 from previous note is intentionally unchanged.

## Bugs / issues registry

### Critical

- [x] SQLite `:memory:` engine/pool behavior can break session visibility across connections.
  - File: `app/db/session.py`
- [x] Migration `normalize_account_plan_types` can fail on older schemas due to ORM column drift.
  - File: `app/db/migrations/versions/normalize_account_plan_types.py`

### High

- [x] OAuth state is globally shared and mutable; flow isolation is weak.
  - File: `app/modules/oauth/service.py`
- [x] Proxy chat/non-stream error semantics collapse too many upstream statuses to `502/503`.
  - File: `app/modules/proxy/api.py`
- [x] Stream retry path after `401` can bypass normal error classification/account handling.
  - File: `app/modules/proxy/service.py`
- [x] Chat tool-call stream assembly has edge-case bugs (indexing, termination handling).
  - File: `app/core/openai/chat_responses.py`
- [x] Settings singleton creation has race (`get_or_create` under concurrency).
  - File: `app/modules/settings/repository.py`
- [x] Frontend settings panel/runtime has blocking state bugs (`hasLoaded`/`isLoading`, missing method).
  - Files: `app/static/index.js`, `app/static/index.html`
- [x] Usage summary capacity can be inflated when accounts have no usage rows.
  - Files: `app/modules/usage/builders.py`, `app/core/usage/__init__.py`

### Medium

- [x] Request ID context is not reset on successful requests.
  - File: `app/core/middleware/request_id.py`
- [x] Exception middleware loses headers/details in some HTTP errors.
  - File: `app/core/handlers/exceptions.py`
- [x] Account import endpoint catches broad `Exception`, masking internal failures.
  - File: `app/modules/accounts/api.py`
- [x] App startup cleanup/static mount resilience can be improved.
  - File: `app/main.py`
- [x] Sticky session timestamp is not touched on pinned-hit path.
  - File: `app/modules/proxy/load_balancer.py`
- [x] Request logs filter edge-cases (`modelOption` parsing, invalid status handling).
  - Files: `app/modules/request_logs/api.py`, `app/modules/request_logs/service.py`, `app/modules/request_logs/repository.py`
- [x] Message coercion text/file alias handling has edge-case gaps.
  - File: `app/core/openai/message_coercion.py`

### Pending backlog

- [x] Firewall proxy-header trust now verifies source proxy network before honoring `X-Forwarded-For`.
  - File: `app/core/middleware/api_firewall.py`
- [x] Quota/status reset logic keeps `RATE_LIMITED` until an active reset boundary when runtime reset is missing.
  - File: `app/core/usage/quota.py`
- [x] Usage updater primary/secondary writes are now atomic (single transaction via batch write).
  - File: `app/modules/usage/updater.py`, `app/modules/usage/repository.py`
- [x] Pricing clamps invalid cached token counts and rejects negative token payloads.
  - File: `app/core/usage/pricing.py`

### Accepted / intentionally not changed

- [x] Open dashboard behavior when TOTP enforcement is off.
- [x] Firewall coverage for `/api/codex/usage`.
- [x] Previously referenced spec item #3 (kept as-is per instruction).
- [x] Chat `input_audio` remains unsupported on `/v1/chat/completions` for compatibility with existing contract/tests.
