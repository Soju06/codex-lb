# Tasks

## 1. Specify overflow routing
- [x] Define aggregate `usage_limit_reached` as the only subscription-overflow trigger.
- [x] Define replay-safety, account/file ownership, API-key scope, and terminal fallback failure behavior.
- [x] Reuse dashboard-managed OpenAI-compatible Model Sources for credentials and transport.

## 2. Model Source fallback configuration
- [ ] Add a single fallback designation and optional fallback model override to Model Sources.
- [ ] Validate that fallback sources are enabled and Responses-capable, and enforce a single configured fallback.
- [ ] Expose fallback configuration in the existing Model Sources dashboard without revealing stored credentials.

## 3. Responses overflow routing
- [ ] Select the configured fallback only after terminal aggregate subscription `usage_limit_reached`.
- [ ] Preserve the requested model or apply the configured fallback model override.
- [ ] Reuse account-neutral replay safety; fail closed for file-pinned or unprovable retained-response state.
- [ ] Preserve API-key source assignment policy and settle the existing reservation exactly once through source forwarding.
- [ ] Keep fallback provider failures terminal rather than looping back into subscription selection.

## 4. Verification
- [ ] Cover account-available, all-accounts-exhausted, unconfigured, local-capacity, unsafe-continuity, file-pinned, model-override, API-key scope, and fallback-error cases.
- [ ] Cover Model Source API/schema validation and migration behavior.
- [ ] Cover dashboard fallback controls.
- [ ] Run OpenSpec validation, Python formatting/lint/type checks used by CI, focused backend tests, and frontend tests/typecheck.
