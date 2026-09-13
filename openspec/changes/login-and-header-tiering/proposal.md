## Why

Since `user-login-and-session-v2` and `manage-dashboard-users-and-invites` the backend signs people in as accounts, tells the client whether the login screen needs a username (`login.username_field`), issues invite links that are accepted at `POST /api/dashboard-auth/invite/accept`, and returns the team-size facts (`access_summary`) that decide how much of the account machinery a dashboard should show. The dashboard still renders the shared-password era: a password-only form that cannot send a username (so a two-account install cannot sign in at all), no page for an invite link, a Logout button that does not say who is signed in, and a store that only knows `read`/`write`. PLAN.md §4.4, §4.5, §4.8 and §4.11 (PR-1d-1) make this the first frontend slice: the login form follows the server hint, `/invite/:token` is a public route rendered before the login branch, the header grows an account menu only once a second person exists, and every disclosure decision comes from one pure function.

## What Changes

- **Session schema and store**: `AuthSessionSchema` accepts the account fields (`user`, `authMethod`, `mustChangePassword`, `totpEnrollmentRequired`, `login`, `accessSummary`, `assignableRoleIds`) with individual-install defaults so payloads without them parse unchanged. The store adds `user`, `loginHint`, `accessSummary`, the derived `tier`, `can(permission)` / `scope(permission)` (read from the `<permission>:<scope>` entries only), `logoutEverywhere()` (`POST /logout-all`) and remembers the typed username. `canWrite` is unchanged.
- **`resolveDisclosureTier(accessSummary, user)`** (`features/auth/disclosure.ts`): pure function; `null` summary → `team` for a signed-in account (it can only exist through an invite), `individual` otherwise; `team` and `enterprise` per PLAN §4.11 with a summary; matrix unit test.
- **Login form**: `login.usernameField=hidden` → password box plus a small "Sign in with a different account" link that reveals the username field; `shown` → username field on top, prefilled from `localStorage["codex-lb.last-username"]` (empty when nothing is stored). Anti-flapping: a remembered non-default username keeps the field visible even when the server says `hidden`; the subtitle asks for username and password whenever the field is shown, and revealing it moves focus into it. `422 username_required` reveals the field with an inline message; `401 invalid_credentials`, `429`, `401 totp_required` keep today's handling. The username is omitted from the request when the field is hidden or blank.
- **Public route `/invite/:token`**: `AuthGate` reads the location and renders `InviteAcceptScreen` before the login branch, for visitors and signed-in accounts alike: checking → form (role, inviter, username prefilled and read-only when locked, display name, password + confirm) → session cookie → `/` (a session that still has to enrol an authenticator lands on the enrollment gate). A `404` shows one message; other lookup failures show the server message with "Try again". Any account session (even one pending TOTP) is told to log out (button), `409 already_signed_in` from accept switches to the same branch, and the accepted session is applied through a store action; `username_taken` / `username_locked` are inline field errors; the client username rule mirrors the backend's.
- **TOTP enrollment**: the setup dialog's QR/secret/code form is extracted into `TotpEnrollmentForm` and reused by the Settings card and by the gate (a session with `totpEnrollmentRequired` is held on an enrollment screen; the confirmed code is verified immediately so the TOTP dialog is not shown twice).
- **Header**: individual tier (or no `user`) keeps today's Logout button unchanged; from the team tier a signed-in account gets an avatar chip `username · role` with My password (existing change-password dialog), My two-factor (`/settings#totp`, only under the predicate that renders the TOTP card), Log out everywhere, Log out. "Invite teammate" arrives with its target card in `access-settings-card`. The mobile sheet keeps its Logout entry.
- **Navigation metadata and route guard**: the nav arrays move to `components/layout/nav-items.ts` (the simplicity-budget config is repointed; still five core items) and each item carries `requires: Permission`; items are filtered by the session's grants and a guarded route the session cannot use redirects to `/dashboard`. Guests keep exactly today's five items.
- **Recovery**: a failed session request renders the route-recovery error surface with a retry; the 401 handler resets to least privilege and re-shows the spinner until the refresh settles.
- **Docs**: two sentences in `docs/authentication.md`.

No new nav items, routes other than the public invite route, settings, env vars, SSO buttons, `/login` route or Access card (those belong to later slices). Individual-tier copy avoids the words user/role/SSO/SCIM/IdP/RBAC.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: public routes rendered outside the auth gate; login form username disclosure; header account menu tiering; navigation permission metadata and route guard (qualifies the header navigation requirement); disclosure tier as a pure function; authenticator enrollment gate.

## Impact

- `frontend/src/features/auth/{schemas,api,disclosure,last-username}.ts`, `hooks/use-auth.ts`, `components/{auth-gate,login-form,invite-accept-screen,totp-enrollment-form,auth-screen-frame}.tsx`
- `frontend/src/components/layout/{app-header,account-menu}.tsx`, `nav-items.ts`, `frontend/src/App.tsx`
- `frontend/src/features/settings/components/totp-settings.tsx` (uses the shared enrollment form)
- `frontend/src/i18n/locales/{en,ko,zh-CN}.json`, `frontend/src/test/mocks/*`, `.github/simplicity-budgets.toml` (path only), `docs/authentication.md`
- Backend contract consumed as shipped: `GET /session` fields, `POST /password/login {username?, password}`, `GET /invite/{token}`, `POST /invite/accept`, `POST /logout-all`, `POST /totp/setup/*`. No backend change.
