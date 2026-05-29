## 1. Backend — Combined export endpoint

- [x] 1.1 Add `AccountAuthExportTokens`, `AccountAuthExportResponse` schemas to `app/modules/accounts/schemas.py`
- [x] 1.2 Add `export_auth()` service method to `app/modules/accounts/service.py` combining logic from `export_account` and `export_opencode_auth` into one method returning tokens + both authJson formats
- [x] 1.3 Add `POST /{account_id}/export/auth` route to `app/modules/accounts/api.py` with no-cache headers and `account_auth_exported` audit event

## 2. Frontend — Schemas and API

- [x] 2.1 Add `AccountAuthExportResponseSchema` and related sub-schemas to `frontend/src/features/accounts/schemas.ts` matching the combined response shape
- [x] 2.2 Add `exportAccountAuth()` function to `frontend/src/features/accounts/api.ts` calling `POST /api/accounts/{id}/export/auth`

## 3. Frontend — Unified AuthExportDialog

- [x] 3.1 Create `frontend/src/features/accounts/components/auth-export-dialog.tsx` — rename and extend `OpenCodeAuthExportDialog` with: title "Auth Export", mode dropdown ("codex"/"opencode" default "codex"), conditional Codex token rows (id_token, access_token, refresh_token), context-aware auth.json block and download filename
- [x] 3.2 Create `frontend/src/features/accounts/components/auth-export-dialog.test.tsx` — test mode switching, codex token previews, download behavior

## 4. Frontend — Hooks and wiring

- [x] 4.1 Add `exportAuthMutation` to `use-accounts.ts` replacing `exportMutation` and `exportOpenCodeAuthMutation` — returns data to caller, toasts success
- [x] 4.2 Update `AccountActions` component — single "Export" button, remove second button and `onExportOpenCodeAuth` prop
- [x] 4.3 Update `AccountDetail` props — remove `onExportOpenCodeAuth`, keep single `onExport`
- [x] 4.4 Update `AccountsPage` — wire single `exportAuthMutation` + `AuthExportDialog`, remove old `OpenCodeAuthExportDialog` import and wiring

## 5. Tests — Backend integration

- [x] 5.1 Add integration test for `POST /export/auth` verifying response shape, token decryption, authJson formats, cache headers, audit event
- [x] 5.2 Add integration test for `POST /export/auth` 404 on missing account

## 6. Tests — Frontend hook tests

- [x] 6.1 Update `use-accounts.test.ts` to test `exportAuthMutation` success and error paths

## 7. Verification

- [x] 7.1 Run `uv run pytest` — all backend tests pass
- [x] 7.2 Run frontend tests — all component and hook tests pass
- [x] 7.3 Run linter — no lint errors
