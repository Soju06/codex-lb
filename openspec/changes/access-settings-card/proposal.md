## Why

`login-and-header-tiering` taught the dashboard who is signed in and how big the team is, but Settings still shows the shared-password era: four separate cards (Guest access, Password, Session, TOTP) and nothing about the other people the backend can now manage (`manage-dashboard-users-and-invites`). The header's "Invite teammate" item has no target. PLAN.md §4.8 and §4.11 (PR-1d-2) fold the four cards into one "Access" card that grows with the install — a single line plus today's controls for one person, a People tab for a team — so an individual install gets simpler (9 → 7 top-level cards) while a team gets the accounts table, invites and a read-only view of the roles without a new navigation item.

## What Changes

- **Access card** (`features/settings/components/access/`): replaces the Guest access, Password, Session and TOTP cards on the Settings page. Gate is the same as those cards had (`write`). Body follows the store's derived `tier` and `can('users:manage')`:
  - `individual`: one line ("Only you are using this dashboard — …" with **Invite a teammate**; or "set a password first" with **Set password** when the session has no account) followed by the four controls rendered by the *same components in the same order* (`AccessMySignInTab`). Trusted-header and disabled-auth installs render no people line.
  - `team` / `enterprise` with `users:manage`: tabs **People** and **My sign-in**; the hash selects the tab (`#access-people` → People, `#access` → My sign-in).
  - `team` / `enterprise` without `users:manage`: the four controls only.
- **People tab**: table (name/username, role badge with the role's permissions on hover, status incl. invite expiry, last sign-in, sign-in method, "you" marker), row actions (change role, disable/enable, reset two-factor, log out everywhere, delete; invited rows: copy new link, revoke), header actions (**Invite**, **Pending invites N** when N > 0, quiet **View roles**), a sign-in requirements line that points at the existing "Require TOTP on login" control, and **View full page** above eight rows. Server refusals (`last_admin_protected`, `insufficient_delegation`, `compat_user_locked`, `invite_pending`, `invite_not_pending`, …) are shown inline. The self row offers no role/disable/delete; the migrated `admin` row offers no delete.
- **Invite dialog**: username, optional display name, role limited to the session's `assignableRoleIds` (Operator preselected, one-line description, warning for Admin, first-invite note about the username field); the link is shown once with a copy button and the 24-hour expiry note. No e-mail is sent.
- **Pending invites sheet** (resend = copy new link, revoke) and **Roles sheet** (read-only preset cards Admin/Operator/Member/Viewer + Guest; Member greyed "Coming later"; no clone/edit controls).
- **`/settings/access`**: the People tab on its own page, guarded by `users:manage`, not a navigation item (core nav stays at five).
- **Deep links**: `advanced-settings-deeplink.ts` gains `accessTabFromHash`; the header account menu points **Invite teammate** at `/settings#access-people` and **My two-factor** at `/settings#access`.
- **Data**: `features/access/{api,hooks}.ts` (TanStack Query over `/api/dashboard-users*` and `/api/dashboard-roles*`); every mutation invalidates the lists and refreshes the session so the card falls back to the individual body when an install returns to one account.
- **Store**: `assignableRoleIds` is kept from the session.
- **Docs**: the "Accounts and invites" section of `docs/authentication.md` names where the People tab lives and the deep links.

Not in this change: an owner column on `/apis` (the API-key list does not expose `owner_user_id` yet), any organisation/SSO group, a clone/edit role control, an admin-only TOTP toggle, new nav items or settings.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: Settings page composition (four cards fold into the Access card); Access card tiering; People tab, invite dialog, sheets; `/settings/access` route outside the nav; deep-link hashes; header account menu targets.

## Impact

- `frontend/src/features/settings/components/access/*`, `frontend/src/features/access/*`, `frontend/src/features/settings/{advanced-settings-deeplink.ts,components/settings-page.tsx}`, `frontend/src/components/layout/account-menu.tsx`, `frontend/src/App.tsx`, `frontend/src/features/auth/hooks/use-auth.ts`
- `frontend/src/i18n/locales/{en,ko,zh-CN}.json`, `frontend/src/test/mocks/*`, `docs/authentication.md`
- Backend contract consumed as shipped: `GET/POST /api/dashboard-users`, `GET /api/dashboard-users/invites`, `PATCH/DELETE /api/dashboard-users/{id}`, `POST/DELETE /api/dashboard-users/{id}/invite`, `POST …/reset-totp`, `POST …/revoke-sessions`, `GET /api/dashboard-roles`, `GET /api/dashboard-roles/permissions`. No backend change.
