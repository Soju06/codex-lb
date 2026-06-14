## Why

Operators saw imported or long-lived accounts become unusable after upstream
session invalidation signals, bursty background refreshes, and stale in-memory
bridge sessions kept routing traffic to accounts that were no longer usable.

The old #804 follow-up mixed these fixes with an account proxy import UI that
does not exist on current #875. This change ports only the pieces that align
with #875's current proxy-pool routing model.

## What Changes

- Treat `app_session_terminated` as a permanent re-authentication-required
  credential/session failure.
- Stagger background usage refresh so each slice handles one eligible account
  instead of refreshing every account in the same burst.
- Track accounts that become routing-unavailable in-process so stale HTTP
  bridge sessions are not reused after deactivation, pause, delete, or failed
  refresh state changes.
- Add a per-account Codex installation id and inject it into Codex upstream
  response-create metadata while stripping inbound client-supplied installation
  ids.

## Non-Goals

- Restore #804's import-time account proxy form fields.
- Restore #804's old per-account proxy dialog.
- Change account import to bind a proxy during `auth.json` upload.

## Capabilities

### Modified Capabilities

- `usage-refresh-policy`
- `account-routing`
- `responses-api-compat`
- `database-migrations`

## Impact

- Backend account status and routing cache behavior.
- Usage refresh scheduler cadence.
- Account schema/migration for `codex_installation_id`.
- Responses HTTP/WebSocket metadata handling and tests.
