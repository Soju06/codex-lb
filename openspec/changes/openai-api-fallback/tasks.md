# Tasks

## 1. Specify overflow routing
- [x] Define aggregate `usage_limit_reached` as the only subscription-overflow trigger.
- [x] Define replay-safety, account/file ownership, API-key scope, and terminal fallback failure behavior.
- [x] Reuse dashboard-managed OpenAI-compatible Model Sources for credentials and transport.

## 2. Model Source fallback configuration
- [x] Add a single fallback designation and optional fallback model override to Model Sources.
- [x] Validate that fallback sources are enabled and Responses-capable, and enforce a single configured fallback.
- [x] Expose fallback configuration in the existing Model Sources dashboard without revealing stored credentials.

## 3. Responses overflow routing
- [x] Select the configured fallback only after terminal aggregate subscription `usage_limit_reached`.
- [x] Preserve the requested model or apply the configured fallback model override.
- [x] Reuse account-neutral replay safety; fail closed for file-pinned or unprovable retained-response state.
- [x] Preserve API-key source assignment policy and settle the existing reservation exactly once through source forwarding.
- [x] Keep fallback provider failures terminal rather than looping back into subscription selection.

## 4. Verification
- [x] Cover account-available, all-accounts-exhausted, unconfigured, local-capacity, unsafe-continuity, file-pinned, model-override, API-key scope, and fallback-error cases.
- [x] Cover Model Source API/schema validation and migration behavior.
- [x] Cover dashboard fallback controls.
- [x] Run OpenSpec validation, Python formatting/lint/type checks used by CI, focused backend tests, and frontend tests/typecheck.
