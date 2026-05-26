# Tasks: add-oauth-account-proxy

## 1. Backend: shared persistence helper

- [x] 1.1 Extract `AccountsService.persist_account_with_optional_proxy(account, proxy_payload, refresh_token)` from the body of `import_account` (`app/modules/accounts/service.py`)
- [x] 1.2 Replace the inline probe+upsert+invalidate block in `import_account` with a call to the helper; behavior unchanged

## 2. Backend: OAuth deferred persistence

- [x] 2.1 Add `expect_proxy: bool = False` to `OauthStartRequest` (`app/modules/oauth/schemas.py`)
- [x] 2.2 Add `pending_tokens: OAuthTokens | None`, `pending_expires_at: float | None`, and `expect_proxy: bool` to `OAuthState` (`app/modules/oauth/service.py`)
- [x] 2.3 In `start_oauth`, persist `expect_proxy` onto `OAuthState` for the new attempt
- [x] 2.4 Extract `_build_account_from_tokens(tokens) -> Account` from the body of `_persist_tokens`
- [x] 2.5 Update `_handle_callback`, `manual_callback`, and `_poll_device_tokens` to:
  - When `state.expect_proxy=False`: persist immediately via the existing path (unchanged)
  - When `state.expect_proxy=True`: store tokens in `state.pending_tokens`, set `pending_expires_at = time.time() + 600`, set status to `tokens_ready`, do NOT persist
- [x] 2.6 In `oauth_status`, when status is `tokens_ready` and `pending_expires_at` is past, reset state and return `error` with a "sign-in expired before finalization" message
- [x] 2.7 Add optional proxy fields (`proxy_host`, `proxy_port`, `proxy_username`, `proxy_password`, `proxy_remote_dns`, `proxy_label`) to `OauthCompleteRequest`; mirror `AccountProxyInput` validation
- [x] 2.8 Rewrite `complete_oauth` so that when `state.status=tokens_ready` and `state.pending_tokens` is set, it:
  - Builds an Account from `pending_tokens` via `_build_account_from_tokens`
  - Calls `accounts_service.persist_account_with_optional_proxy(account, proxy_payload, refresh_token)`
  - Clears `pending_tokens`, sets status to `success`
  - On `ProxyProbeError`: keeps `pending_tokens`, keeps status `tokens_ready`, propagates the error
- [x] 2.9 Inject `AccountsService` into `OauthContext`/`OauthService` constructor wherever the context is wired (`app/main.py` or `app/modules/oauth/dependencies.py`)

## 3. Backend: API surface

- [x] 3.1 In `app/modules/oauth/api.py`, add exception handlers to `POST /api/oauth/complete` for `ProxyProbeError` (422 `proxy_probe_failed` with typed reason) and `ValidationError` (422 `validation_error`); mirror `app/modules/accounts/api.py`
- [x] 3.2 Emit `account_proxy_set` audit log on successful OAuth+proxy persistence (host, port, label) — symmetric with `import_account`

## 4. Frontend: schemas and API client

- [x] 4.1 Add `"tokens_ready"` to the `OAuthStateSchema` status enum (`frontend/src/features/accounts/schemas.ts`)
- [x] 4.2 Extend `OauthStartRequestSchema` with `expectProxy` and the proxy fields used by `/api/oauth/start`
- [x] 4.3 Extend `OauthCompleteRequestSchema` with the optional proxy fields (mirror `AccountProxyInputSchema` field-by-field; do not embed the schema directly)
- [x] 4.4 Update `startOauth` and `completeOauth` in `frontend/src/features/accounts/api.ts` to forward the new fields

## 5. Frontend: shared ProxyFormSection

- [x] 5.1 Create `frontend/src/features/accounts/components/proxy-form-section.tsx` containing the collapsible proxy form (host, port, username, password, label, remote_dns switch) extracted from `import-dialog.tsx`
- [x] 5.2 Update `import-dialog.tsx` to use the new shared component; verify no visual or behavioral regressions
- [x] 5.3 Component exposes: current values, `showProxy` toggle, validation result via `AccountProxyInputSchema`, disabled state

## 6. Frontend: OAuth dialog

- [x] 6.1 Add `"tokens_ready"` to the `Stage` union and `getStage` in `oauth-dialog.tsx`
- [x] 6.2 Render `ProxyFormSection` with the Browser/Device options; keep it rendered on browser/device stages so the selected proxy remains visible during the wait
- [x] 6.3 When the user picks a method, pass `expectProxy=showProxy` through `start()` to `startOauth`
- [x] 6.4 Add a `tokens_ready` stage UI: "Sign-in complete. Click Finish to validate proxy and save." with a "Finish setup" button. Disabled while the proxy form is invalid (mirrors import dialog button-disable logic)
- [x] 6.5 On Finish click, call `complete()` with the validated proxy payload
- [x] 6.6 Surface `proxy_probe_failed` errors via `formatProbeError`; keep dialog at `tokens_ready` so the user can retry
- [x] 6.7 Auto-complete behavior for `showProxy=false`: when poll status becomes `success` directly (today's path), the dialog still auto-shows success without requiring a Finish click

## 7. Frontend: hook

- [x] 7.1 Update `use-oauth.ts` `start()` to accept `expectProxy: boolean`
- [x] 7.2 Update `complete()` to accept an optional `proxy?: AccountProxyInput` and forward to `completeOauth`
- [x] 7.3 Update the poll loop to transition state when status is `tokens_ready`

## 8. Tests: backend

- [x] 8.1 Unit test for `AccountsService.persist_account_with_optional_proxy`: no-proxy path, with-proxy success, with-proxy probe failure, identity conflict
- [x] 8.2 Unit test for OAuth service: `expect_proxy=false` preserves today's auto-persist for all three arrival paths
- [x] 8.3 Unit test: `expect_proxy=true` defers persistence on all three arrival paths; status becomes `tokens_ready`
- [x] 8.4 Unit test: `complete_oauth` with proxy probes + persists atomically; account becomes ACTIVE with proxy configured
- [x] 8.5 Unit test: probe failure preserves `pending_tokens` and `tokens_ready` status; retry with corrected proxy succeeds without re-auth
- [x] 8.6 Unit test: TTL expiry on `pending_tokens` resets state to error
- [x] 8.7 Unit test: concurrent token arrival (e.g., browser callback + manual callback) is idempotent
- [x] 8.8 Integration test: end-to-end `POST /api/oauth/start` with `expect_proxy=true` → poll → `POST /api/oauth/complete` with proxy → 200; error envelope on probe failure matches `accounts/api.py` shape

## 9. Tests: frontend

- [x] 9.1 `oauth-dialog.test.tsx`: proxy section renders on intro/browser/device/tokens_ready stages; `tokens_ready` shows Finish button; Finish click forwards proxy fields
- [x] 9.2 `use-oauth` test: `start` forwards `expectProxy`; `complete` forwards proxy payload; `tokens_ready` triggers a stage transition
- [x] 9.3 `proxy-form-section.test.tsx` (or covered transitively via import/oauth dialog tests): validation, disabled state, default values

## 10. Specs

- [x] 10.1 Write delta in `openspec/changes/add-oauth-account-proxy/specs/account-egress-proxy/spec.md` (new scenario for OAuth-with-proxy atomicity)
- [x] 10.2 Write delta in `openspec/changes/add-oauth-account-proxy/specs/frontend-architecture/spec.md` (modified Device-OAuth scenario, new OAuth-with-proxy scenario)
- [x] 10.3 After implementation, sync deltas into main `openspec/specs/**/spec.md`

## 11. Verification

- [x] 11.1 `uv run ruff check` and `uv run pyright app/`
- [x] 11.2 `cd frontend && npm run typecheck`
- [x] 11.3 `uv run pytest tests/integration/test_oauth_flow.py tests/integration/test_accounts_proxy_api.py tests/unit/test_account_proxy_schemas.py tests/unit/test_account_http_client.py tests/unit/test_proxy_websocket_client.py -q`
- [x] 11.4 `cd frontend && npm test` (filter to oauth + accounts tests)
- [x] 11.5 Manual UI: start dev server, exercise OAuth+proxy flow with a deliberately wrong proxy first to confirm typed-error rendering and `pending_tokens` retry behavior; then correct and confirm successful atomic persistence with no unproxied refresh in logs
- [x] 11.6 Run `/opsx:verify add-oauth-account-proxy`
