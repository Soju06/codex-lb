## Why

A dashboard session cookie is a bearer token: whoever holds it can do everything the account can, including the handful of changes that decide who may sign in at all (guest password, TOTP requirement, sign-in providers, accounts and roles) and the export of upstream account credentials. PLAN.md §5 H5 (PR-2b) puts a second lock on exactly those changes: the person has to re-prove a credential within the last five minutes ("step-up"). Because `auth-provider-abstraction` made reverse-proxy identities real accounts, the rule has to work for accounts that hold no password at all — they get their own authenticator secret as the thing to re-prove — and it must never be silently waived for anyone.

## What Changes

- **Enforcement**: `require_dashboard_permission(perm)` for `perm` in `security:write`, `users:manage`, `roles:manage`, `accounts:export` (`STEP_UP_PERMISSIONS`) additionally requires a step-up recorded within the last 300 s for non-safe methods; `PUT /api/settings` requires it only when the body changes a security field (the existing changed-field detection). Missing or stale → `403 step_up_required` with `param` = the permission and `details.methods` = the factors the account can present; an account holding neither a password nor a TOTP secret → `403 step_up_unavailable`. Reads, and principals without an account (the implicit local admin, the disabled-auth principal), are not gated. A provider's `idp_mfa_enforced` does not waive step-up.
- **State**: the v2 session cookie gains an optional `su` claim (unix seconds of the last step-up). It is minted when the sign-in itself presented every factor the account holds: password login or invite acceptance for an account without a TOTP secret, `/totp/verify` otherwise. Trusted-header accounts carry no session cookie, so their step-up rides in a separate five-minute cookie `codex_lb_step_up` (`{v:1, uid, su, exp}`, HttpOnly, SameSite=Lax, Secure on HTTPS), honoured only for the account it names.
- **Endpoint**: `POST /api/dashboard-auth/step-up` `{password?, code?}` for any signed-in account principal. Password accounts present the password (and the TOTP code when they have a secret); password-less accounts present the code. Every refusal is `401 invalid_credentials`; attempts spend the per-client password budget (8/60 s, `429 step_up_rate_limited`). Success re-issues the session cookie with `su` (same method, TOTP step and remaining life) or sets the step-up cookie for header accounts, audits `step_up_verified`, and returns `{verifiedAt, expiresAt}`.
- **TOTP for password-less accounts**: `/totp/setup/start`, `/totp/setup/confirm`, `/totp/verify` and `/totp/disable` accept a trusted-header account without a password session; `/totp/verify` for such an account mints the step-up cookie; `/totp/disable` answers `{status, stepUpAvailable}` so the client can say when the account just lost its only step-up method.
- **Session response**: `step_up: {verified_at, expires_at, methods}` for signed-in accounts.
- **Frontend**: the API client runs one shared step-up dialog on `403 step_up_required` (password and/or authenticator fields chosen from `details.methods`) and replays the interrupted request once; `403 step_up_unavailable` shows the enrol-two-factor toast with a link to My sign-in. The Access card's My sign-in tab shows the TOTP control to every account (a reverse-proxy account enrols there); the TOTP status follows the session's account, not the settings row.
- **Docs**: `docs/authentication.md` gains "Confirming sensitive changes".

Not in this change: the admin-role TOTP requirement (PR-2a), OIDC `prompt=login`/`max_age` as a step-up factor (Phase 3a), any new setting (the window is a constant).

## Capabilities

### Modified Capabilities

- `admin-auth`: step-up requirement, endpoint, cookie and `su` claim; TOTP routes for password-less accounts; `step_up` in the session response.
- `frontend-architecture`: step-up dialog with transparent retry; My sign-in TOTP control for every account.

## Impact

- `app/core/auth/{step_up.py,dashboard_access.py,dependencies.py}`, `app/core/{exceptions,errors}.py`, `app/core/handlers/exceptions.py` (`details` in the dashboard error envelope), `app/modules/dashboard_auth/{api,schemas,service}.py`, `app/modules/settings/api.py`
- `frontend/src/lib/api-client.ts`, `frontend/src/schemas/api.ts`, `frontend/src/features/auth/{api,schemas,hooks/use-auth,components/step-up-dialog}.ts(x)`, `frontend/src/features/settings/components/{access/access-my-sign-in-tab,totp-settings}.tsx`, `frontend/src/App.tsx`, `frontend/src/i18n/locales/*`
- `docs/authentication.md`; tests under `tests/unit`, `tests/integration`, `frontend/src/**/*.test.ts(x)`
- No new `CODEX_LB_*` setting, no schema change, no migration.
