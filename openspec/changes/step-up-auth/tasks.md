## 1. Backend

- [x] 1.1 `app/core/auth/step_up.py` (window, cookie name, `step_up_methods`, freshness), `STEP_UP_PERMISSIONS`, `DashboardPrincipal.step_up_verified_at`; `details` on `AppError` and the dashboard error envelope.
- [x] 1.2 Session cookie `su` (store, state, minted at password login / invite accept without a secret and at `/totp/verify`, copied on password change); `StepUpCookieStore` and `session_clock`.
- [x] 1.3 `ensure_step_up` in the permission dependency (non-safe methods, account principals only, `step_up_required` with `details.methods`, `step_up_unavailable`) and in `PUT /api/settings` on security-field changes.
- [x] 1.4 `POST /api/dashboard-auth/step-up` (factors per account, `invalid_credentials`, password limiter, cookie re-issue or step-up cookie, audit `step_up_verified`); TOTP setup/confirm/verify/disable for trusted-header accounts; `stepUpAvailable` on disable; `step_up` in the session response.

## 2. Frontend

- [x] 2.1 API client: `StepUpHandlers`, one-shot replay on `step_up_required`, `onUnavailable`; error schema carries `param`/`details`.
- [x] 2.2 `StepUpDialog` (password and/or code from `details.methods`, refreshes the session, shared by concurrent requests) mounted in `App`; store `stepUp`; `stepUp()` API; i18n en/ko/zh-CN; MSW handler.
- [x] 2.3 My sign-in tab shows the TOTP control for every account; TOTP status follows the session.

## 3. Verification and docs

- [x] 3.1 Unit: methods matrix, window, cookie decode/uid mismatch/expiry, `su` in the session cookie. Integration: password account (403 → step-up → 200, wrong password, limiter, reads and other writes untouched, session `stepUp`, audit, expiry), password+TOTP (both factors, replay refused, `su` at verify), invite acceptance mints `su`, implicit admin exempt, header account (`step_up_unavailable` → enrol → `step_up_required` → code → cookie → 200, cookie bound to the account, expiry, disable reports availability, `/totp/verify` mints the cookie); route matrix marks the step-up-gated routes.
- [x] 3.2 Frontend: client replay/cancel/no-loop/unavailable, dialog opens on 403 and replays, TOTP-only dialog; lint, typecheck, full vitest.
- [x] 3.3 `docs/authentication.md` "Confirming sensitive changes"; `openspec validate step-up-auth --strict`.
