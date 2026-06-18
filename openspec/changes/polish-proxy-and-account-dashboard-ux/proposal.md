# Change: polish proxy and account dashboard UX

## Why

PR #912 shipped upstream proxy dashboard controls, but the UX is poor: the Settings proxy
section renders three heavy creation forms (endpoint, pool, pool-member) permanently inline,
cluttering the page like exposed modals, while account routing-policy and proxy-pool selectors
use arbitrary fixed or unconstrained widths that look broken. These new surfaces also lack the
visual consistency of the rest of the dashboard. This change refines the presentation and
interaction of the proxy admin and account controls without altering any backend behavior.

## What Changes

- Refactor the Settings upstream-proxy section so endpoint creation, pool creation, and
  pool-member addition each move into a **triggered modal dialog** opened from explicit buttons,
  instead of being rendered as always-visible inline forms.
- Keep the always-visible proxy section as a compact **summary + management view** (routing
  toggle, default-pool selector, and readable lists of configured endpoints and pools).
- Fix the account **routing-policy** select (remove arbitrary `w-44`) and the account
  **proxy-pool** select so both size predictably within their container and truncate long labels
  gracefully instead of overflowing or collapsing.
- Apply a consistency/polish pass over the accounts page detail panel and list controls
  (spacing, control sizing, empty states) so the proxy additions match the surrounding dashboard.
- Replace the account list's always-visible **Import** and **Add Account** buttons with a single
  dashed-border **"Add account" placeholder** at the bottom of the list that opens a **chooser
  dialog**; the chooser lets the operator pick between adding an account via OAuth or importing an
  `auth.json` file, then opens the corresponding existing dialog.
- Move the account **status filter** onto the **"Need help?"** row so the search input spans the
  full width of the list controls.
- Replace the account-detail **"Account alias" form card** with an **inline editor**: the detail
  header shows the local label (the alias when set) next to a **pencil** button that switches the
  name into an inline input (save/cancel via Check/X, Enter/Escape) for editing the alias.
- No backend, API, schema, or routing changes; this is presentation and interaction only.

## Capabilities

### New Capabilities

<!-- None. This change refines presentation of an existing dashboard capability. -->

### Modified Capabilities

- `frontend-architecture`: Add presentation/interaction requirements for (1) proxy-admin
  creation flows presented as modal dialogs behind trigger buttons, (2) the proxy-admin section
  rendering a summary/management view of configured endpoints and pools, (3) predictable,
  container-responsive sizing for account routing-policy and proxy-pool controls, (4) a single
  account-list add-account entry point that opens a chooser dialog (add via OAuth vs import),
  (5) the account status filter sharing the "Need help?" row with a full-width search input, and
  (6) the account alias edited inline from the detail header via a pencil affordance rather than a
  separate form.

## Impact

- Frontend only (`frontend/`, built into `app/static`):
  - `frontend/src/features/settings/components/upstream-proxy-settings.tsx` (refactor to summary + dialog triggers)
  - New dialog components for endpoint / pool / pool-member creation under `features/settings/components/`
  - `frontend/src/features/accounts/components/account-actions.tsx` (routing-policy select sizing)
  - `frontend/src/features/accounts/components/account-proxy-binding.tsx` (proxy-pool select sizing)
  - `frontend/src/features/accounts/components/account-detail.tsx` (detail-panel polish; inline
    alias editor in the header replacing the alias form card)
  - Removed `frontend/src/features/accounts/components/account-alias-form.tsx` (superseded by the
    inline alias editor)
  - `frontend/src/features/accounts/components/account-list.tsx` (status filter moved to the
    "Need help?" row; Import/Add Account buttons replaced by a bottom "Add account" placeholder)
  - New `frontend/src/features/accounts/components/add-account-dialog.tsx` (add-account chooser dialog)
  - Associated Vitest component tests and any MSW handlers for the new dialog flows
- No backend, database, or API contract changes. Operator-facing API calls and payloads are unchanged.
