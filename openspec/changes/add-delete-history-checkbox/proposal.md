## Why

When an operator deletes an account, request logs are currently soft-deleted (account_id=NULL, deleted_at=now()). There is no way to permanently remove the associated request logs and other history. Operators who want to clean up all traces of a removed account need an explicit opt-in to hard-delete all associated data.

## What Changes

- Add a "Delete all history for this account" checkbox to the existing delete confirmation dialog (`ConfirmDialog`). The dialog already has a dimmed backdrop (`AlertDialogOverlay` with `bg-black/50`), satisfying the UX requirement.
- Extend `ConfirmDialog` with an optional `children` prop that renders between description and footer, so the checkbox can be composed in without forking the dialog component.
- Pass a `delete_history` query parameter on the `DELETE /api/accounts/{account_id}` request when the checkbox is checked.
- Backend: when `delete_history=true`, hard-delete `request_logs` (instead of the current `UPDATE ... SET account_id=NULL, deleted_at=NOW()`) in addition to the existing hard-deletes of `usage_history` and `sticky_sessions`. When false/absent, behavior is unchanged.

## Impact

- The existing delete flow is unchanged when the checkbox is not checked.
- `ConfirmDialog` gains a `children` prop; existing call sites need no changes.
- No database migration required — the delete query behavior changes but the schema does not.
