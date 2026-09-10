## Why

When the first `GET /api/settings` request fails, the Settings page renders its
loading skeleton forever. `SettingsPage` gates its whole body on `!settings`, so
the skeleton branch wins before the error alert can render. The operator sees a
permanently loading page with no message and no way to retry short of reloading
the browser.

## What Changes

- Settings distinguishes a still-pending first load from a failed first load.
  Pending keeps the skeleton; failed replaces it with the error text plus a
  Retry control.
- Retry refetches the settings detail query in place, without a page reload.
- A settings fetch failure that happens while cached settings are already
  displayed keeps the form rendered and shows the error above it, unchanged
  from today.
- Adds `settings.toasts.loadFailed` copy for `en` / `ko` / `zh-CN` as the
  fallback message when the failure carries no message of its own.

## Capabilities

### New Capabilities

- None

### Modified Capabilities

- `frontend-architecture`: the Settings page gains a terminal first-load error
  state with an accessible Retry action, and scopes its page skeleton to the
  pending first load.

## Impact

Dashboard SPA only: `SettingsPage` render branching and i18n (`en`/`ko`/`zh-CN`).
No API, schema, hook contract, database, proxy, or nav-budget changes. The
`useSettings` hook already returns `isPending`, `isFetching`, and `refetch`, so
no hook change is required.
