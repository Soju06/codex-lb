## 1. Proxy admin creation dialogs

- [x] 1.1 Add a create-endpoint dialog component (controlled `open`/`onOpenChange`, react-hook-form + zod, calls `onCreateEndpoint`, closes on success, stays open on error) following the `api-key-create-dialog` pattern
- [x] 1.2 Add a create-pool dialog component (name + endpoint-member selection, calls `onCreatePool`) with the same dialog/controlled pattern
- [x] 1.3 Add an add-pool-member dialog component (pool + endpoint selectors, duplicate-member guard, calls `onAddPoolMember`) with the same dialog/controlled pattern

## 2. Settings upstream proxy section refactor

- [x] 2.1 Refactor `upstream-proxy-settings.tsx` to remove the always-visible 3-column creation forms and instead render trigger buttons that open the dialogs from section 1
- [x] 2.2 Keep the summary/management view: routing toggle, default-pool selector, endpoint list (scheme/host/port), pool list (active state + endpoint count)
- [x] 2.3 Add explicit empty states for the endpoint list and pool list when none are configured
- [x] 2.4 Lift dialog open-state into the section (via `useDialogState`) and wire busy/error handling to the existing mutations

## 3. Account control sizing

- [x] 3.1 `account-actions.tsx`: replace the routing-policy `SelectTrigger` fixed `w-44` with `min-w-32 flex-1` (container-responsive + usable minimum), keep accessible label
- [x] 3.2 `account-proxy-binding.tsx`: constrain the proxy-pool `SelectTrigger` (`w-full min-w-0 sm:flex-1`) so long pool labels truncate within the trigger
- [x] 3.3 Verify selected long labels truncate with ellipsis (via select.tsx `line-clamp-1`) and selectors do not overflow on narrow detail panels

## 4. Accounts page polish

- [x] 4.1 Apply consistent control sizing/spacing across the account detail panel sections (alias form + proxy binding aligned to `h-8`/`bg-muted/30` tokens)
- [x] 4.2 Add dark-mode variants to routing-policy badges in the account list item (previously light-only); list/empty states reviewed and already consistent

## 5. Account list add-account flow

- [x] 5.1 Move the account status filter onto the "Need help?" row and let the search input span the full width of the list controls
- [x] 5.2 Replace the inline Import / Add Account buttons with a single dashed-border "Add account" placeholder rendered at the bottom of the account list
- [x] 5.3 Add `add-account-dialog.tsx` chooser (two option cards: add via OAuth, import `auth.json`) that closes itself and opens the existing OAuth / import dialog via the existing `onOpenImport` / `onOpenOauth` callbacks
- [x] 5.4 Defer the follow-on dialog open by one animation frame so the chooser→dialog handoff does not leave Radix `pointer-events: none` stuck on `<body>`

## 6. Account alias inline editing

- [x] 6.1 Replace the `AccountAliasForm` card with an inline `AccountNameField` in the detail header: the `<h2>` shows the local label (`alias || displayName || email`) next to a ghost `Pencil` button (`aria-label="Edit alias"`)
- [x] 6.2 Edit mode swaps the name for an `Input` (`aria-label="Account alias"`, autofocus, `maxLength` 255) with ghost `Check`/`X` buttons; Enter saves, Escape cancels, empty input clears, Save disabled while `busy`; saves via the existing `onSetAlias`
- [x] 6.3 Show the email as the muted subtitle whenever an alias is set (preserve privacy-blur + `showAccountId` ID suffix); do not blur the alias label; keep the helper text "Use a local label to distinguish accounts that share the same email."
- [x] 6.4 Delete the now-unused `account-alias-form.tsx`

## 7. Tests and verification

- [x] 7.1 Rewrote `upstream-proxy-settings.test.tsx` for the dialog flow (open trigger → fill → submit), added hidden-fields-on-initial-render, listing, and empty-state tests
- [x] 7.2 Sizing contract covered by existing role/aria-based `account-actions` / `account-proxy-binding` tests (no `w-44`; accessible names preserved); `account-list` tests cover the chooser + filter placement
- [x] 7.3 Updated the `accounts-flow.test.tsx` alias flow for the inline editor (Edit alias → type → Save alias → re-edit → clear) with equivalent assertions
- [x] 7.4 `bun run lint`, `bun run typecheck`, `bun run test` (518 passed), and `bun run build` all clean
- [x] 7.5 `openspec validate --strict polish-proxy-and-account-dashboard-ux` → valid
