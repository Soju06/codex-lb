## 1. Schema, store, tier

- [x] 1.1 Extend `AuthSessionSchema` with `user`, `authMethod`, `mustChangePassword`, `totpEnrollmentRequired`, `login`, `accessSummary`, `assignableRoleIds` (all defaulted, not strict); `LoginRequestSchema.username?`; invite description / accept schemas.
- [x] 1.2 `features/auth/disclosure.ts` with `resolveDisclosureTier(accessSummary, user)` and a matrix unit test (every rule toggled individually, `null` → individual without an account, team with one).
- [x] 1.3 Store: `user`, `loginHint`, `accessSummary`, `tier`, `can`/`scope` (+ `hasPermission`, `usePermission`), `login(password, username?)`, `acceptInvite`, `logoutEverywhere`; least-privilege defaults and 401 handler (`initialized: false`) reset the new fields; remember the typed username.
- [x] 1.4 API: `logoutAll`, `describeInvite`, `acceptInvite`; MSW handlers and coverage list.

## 2. Screens

- [x] 2.1 Login form: hidden/shown username field, "different account" link, localStorage prefill, anti-flapping (non-default username), focus on reveal, username+password subtitle, `username_required` inline reveal, omit blank username.
- [x] 2.2 `TotpEnrollmentForm` extracted from the Settings TOTP dialog; Settings uses it; gate holds `totpEnrollmentRequired` sessions on an enrollment screen and verifies the confirmed code before applying the session; session-fetch failure renders the retryable error surface.
- [x] 2.3 `InviteAcceptScreen` (checking → form → store `acceptInvite` → `/`, 404 expiry message, retryable lookup errors, signed-in branch for any `user` session and for `409 already_signed_in`, locked username read-only, inline `username_taken` / `username_locked`, client username rule; enrollment handled by the gate after navigation) and the `AuthGate` public-route branch keyed by token.
- [x] 2.4 `AuthScreenFrame` shared by login, invite and enrollment screens.

## 3. Header and navigation

- [x] 3.1 `nav-items.ts` with `requires: Permission` per item and `routePermission()`; header filters items; `.github/simplicity-budgets.toml` repointed (still 5).
- [x] 3.2 `AccountMenu` (chip `username · role` named by its visible text, My password → change-password dialog, My two-factor → `/settings#totp` under the TOTP-card predicate, Log out everywhere, Log out; keyboard operable); individual tier keeps the Logout button.
- [x] 3.3 `RouteGuard` in `App.tsx` (redirect to `/dashboard`, not-found when even that is missing).

## 4. Verification

- [x] 4.1 Unit tests: disclosure matrix; schema defaults for old payloads; login form disclosure cases; store `can`/`scope`/tier/username memory/`logoutEverywhere`; invite screen states; gate public route (visitor and signed-in) and enrollment; header individual vs team, permission-gated items, logout-everywhere; nav filtering.
- [x] 4.2 Integration: guarded route redirects and hides its nav item; existing auth/guest flows updated to the scoped permission fixtures.
- [x] 4.3 `bun run lint`, `bun run typecheck`, `bun run test`, `openspec validate login-and-header-tiering --strict`, `.github/scripts/check_simplicity_budgets.py`.
- [x] 4.4 Before/after screenshots (login individual/team, invite form/expired, header individual/team) with the Playwright harness (P5; kept out of the repo).

## 5. Documentation

- [x] 5.1 Two sentences in `docs/authentication.md` (username field once a second account exists; invite links open at `/invite/<token>`).
