## Why

Operators can already prefer accounts whose quota resets earlier, but the
preference was fixed to the secondary quota window. Some deployments need to
burn short primary-window capacity first, while others need to keep the weekly
secondary-window behavior.

## What Changes

- Add a `prefer_earlier_reset_window` dashboard setting with `primary` and
  `secondary` values.
- Persist the setting in the dashboard settings table and expose it through the
  settings API/import payload.
- Thread the selected reset window through HTTP, WebSocket, bridge, compact,
  transcribe, and sticky fallback account selection.
- Add dashboard controls and status display for the selected reset window.

## Impact

- Existing installations default to `secondary`, preserving prior behavior.
- Operators who choose `primary` spend accounts with sooner primary-window
  resets before accounts whose primary quota resets later.
- The new setting is validated at API and frontend boundaries so unsupported
  values are rejected instead of silently changing routing behavior.
