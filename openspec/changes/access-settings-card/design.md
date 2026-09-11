## Context

The Settings page renders Guest access, Password, Session and TOTP as four top-level cards gated on `canWrite`, plus the API keys, Telemetry and Advanced groups. The store now carries `user`, `accessSummary`, the derived `tier` and `can(permission)`; the backend exposes the accounts, invites and roles APIs. The header account menu already links to `/settings#access` for a card that does not exist yet.

## Goals / Non-Goals

**Goals**
- An individual install looks the same except that four cards became one; no new word, control or request appears for it.
- A team gets everything the accounts API allows — and nothing it does not — inside Settings, with one escape hatch page for large teams.
- The four moved controls keep their components, props, copy and tests.
- Every disclosure decision reads the store's `tier` and `can()`; no component combines summary facts.

**Non-Goals**
- Owner/creator column on `/apis` (the API-key response has no owner fields yet), organisation group, SSO, custom roles, admin-only TOTP requirement, own-scoped Member views, e-mail delivery.

## Decisions

### Components move, files stay

`AccessMySignInTab` renders the existing `GuestAccessSettings`, `PasswordSettings`, `SessionSettings` and `TotpSettings` from their current paths with the same gates the Settings page applied (`canWrite`, `passwordManagementEnabled`, `passwordSessionActive`). Their tests and the Settings page test's mocks keep working, and the before/after screenshots differ only by the enclosing card.

### Tier and permission are both required for People

`resolveDisclosureTier` may report `team` for accounts without `users:manage` (they are team members without a summary). The People tab therefore renders only when `tier !== "individual" && can("users:manage")`; a Viewer or Operator on a team sees the card with the sign-in controls only. The individual body (solo line + controls) is used only on `tier === "individual"`; there the line itself is further gated on `users:manage` and `authMode === "standard"`, and switches to "set a password first" when the session has no `user` (the backend answers `409 admin_account_required` in that state).

### Hash selects the tab, a click overrides it until the next navigation

`accessTabFromHash` lives next to `shouldExpandAdvancedSettings` so Settings has one deep-link module. The card derives the tab from the hash and remembers a click together with the hash it overrode, so a later navigation to `#access-people` wins again without an effect that sets state. The header sends Invite teammate to `#access-people` and My two-factor to `#access`.

### Row actions offer only what the server accepts

Self rows get Log out everywhere only (role, status, delete and own-TOTP reset are `self_modification_forbidden`), and that action goes through the store's `logoutEverywhere()` so the client signs out cleanly instead of receiving a 401 after an admin call. Invited rows get invite actions only. The migrated `admin` row keeps its role and status and cannot be deleted (`compat_user_locked`), and its TOTP cannot be reset while the configured "require TOTP on login" policy is on — the policy is read from the settings response, because the session's `totpRequiredOnLogin` is the per-login challenge flag and is false once the admin has passed TOTP. Every other refusal (`last_admin_protected`, `insufficient_delegation`, …) is a server decision surfaced inline through one `accessErrorMessage` mapping; the UI does not predict them. The MSW handlers mirror these refusals so the tests exercise the gates against the same answers the backend gives.

### The invite flow outlives the tier flip

`useAccessMutations` invalidates the users/invites/roles queries after every mutation and refreshes the session for everything except account creation. The first invite turns an individual install into a team: if the session were refreshed inside `onSuccess`, the solo body — and the dialog inside it — would unmount before the one-time link rendered. The Access card (and `/settings/access`) therefore own `inviteOpen`/`issued` and render `InviteDialog` and `IssuedLinkDialog` outside the tier-dependent body; the session is refreshed when the link dialog is dismissed. An install that revokes its only invite still falls back to the individual body on the same screen.

### People only for accounts the backend can attribute

`tier !== "individual" && can("users:manage")` is not enough: the implicit local admin, trusted-header and disabled-auth principals have no `user` and every people mutation answers `409 admin_account_required`. The People tab and `/settings/access` therefore also require `user !== null && authMode === "standard"`; those principals get their own sign-in controls only.

### Tab clicks are per history entry

The click override is remembered with `useLocation().key`, so a new navigation — even to the same hash, as the header shortcut produces — re-applies the hash rule. The tabs are Radix `Tabs` for the ARIA/keyboard wiring, styled with the same classes.

### `/settings/access` is a route, not a nav item

The route sits under the existing `/settings` guard (`dashboard:read`) and additionally redirects to `/settings` without `users:manage`. It renders the same `AccessPeopleTab` with `fullPage`, which hides the "View full page" link and turns the sign-in requirements action into a link back to `/settings#access`.

## Risks / Trade-offs

- [Deviation] Size: production code is ≈ +1.39k net lines against the plan's ~750 for PR-1d-2. It is still a single capability (the Access card), split exactly along the §4.8 file list (`AccessCard` / `AccessSoloBody` / `AccessPeopleTab` + `InviteDialog`, `PendingInvitesSheet`, `RolesSheet` / `AccessMySignInTab`) plus the data layer; the natural cut if the ceiling is enforced is the two sheets and the row dialogs.

- [Risk] The Access card fetches four lists when the People tab shows. → Only for `users:manage` holders on a team; guests and individual installs issue no new request (asserted by the guest-surface integration test).
- [Trade-off] The "Set password" button in the solo line duplicates the Password control's button below it. → PLAN §4.11 asks for the call to action next to the sentence; both open the same dialog.
- [Trade-off] No owner column on `/apis` in this slice. → `ApiKeyResponse` carries no `owner_user_id`; rendering a column the API cannot fill would be a stub.
