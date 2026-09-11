## 1. Data layer

- [x] 1.1 `features/access/api.ts`: zod shapes for users, invites, roles, permission descriptors; fetchers for every consumed route; `inviteLinkFor`.
- [x] 1.2 `features/access/hooks.ts`: `useDashboardUsers`, `usePendingInvites`, `useDashboardRoles`, `usePermissionDescriptors`, `useAccessMutations` (invalidate + session refresh), `accessErrorMessage`.
- [x] 1.3 Store keeps `assignableRoleIds`; deep-link module gains `accessTabFromHash`, `ACCESS_HASH`, `ACCESS_PEOPLE_HASH`.

## 2. Access card

- [x] 2.1 `AccessCard` shell (title, tier/permission switch, hash-selected tabs, scroll on `#access*`).
- [x] 2.2 `AccessSoloBody` (solo line / set-password line, invite dialog) and `AccessMySignInTab` (the four moved controls, same gates and order).
- [x] 2.3 `AccessPeopleTab` (table, statuses, role badge tooltip, header actions, sign-in requirements line, full-page link) and `PeopleRowActions` (menu, change-role dialog, delete confirmation).
- [x] 2.4 `InviteDialog` (+ `InviteLinkPanel`, `IssuedLinkDialog`), `PendingInvitesSheet`, `RolesSheet`.
- [x] 2.5 Settings page renders the card in place of the four cards; `/settings/access` route and `AccessPage`; account menu deep links.

## 3. Verification

- [x] 3.1 Unit tests: card tiering and order of the four controls, set-password state, tabs and hash deep links, people table, row-action gating (self/invited/compat admin), inline 409/403 handling, change role, delete, resend/revoke, roles sheet, full-page link, invite dialog (default role, admin warning, first-invite note, link once, `username_taken`), `/settings/access` guard, header deep links, deep-link helper.
- [x] 3.2 Existing settings and header tests stay green; MSW handlers + coverage list for the new routes.
- [x] 3.3 `bun run lint`, `bun run typecheck`, `bun run test`, `openspec validate access-settings-card --strict`, simplicity budget check (nav still five).
- [x] 3.4 Before/after screenshots (Settings individual, Access card People tab, invite dialog, roles sheet, `/settings/access`) with the Playwright harness; individual Settings page identical apart from the fold.

## 4. Documentation

- [x] 4.1 "Accounts and invites" in `docs/authentication.md`: where the People tab lives, the deep links, the full page.
