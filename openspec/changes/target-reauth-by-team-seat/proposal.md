## Why

Multiple ChatGPT Team or Business seats can share one upstream
`chatgpt_account_id` while representing different user principals. Treating that
workspace identifier as a unique reauthentication identity can update the wrong
local row when the browser completes OAuth as another seat in the same
workspace.

## What Changes

- Persist the seat-specific `chatgpt_user_id` exposed by OAuth claims separately
  from the shared workspace `chatgpt_account_id`.
- Carry the selected local account ID in server-side OAuth flow state for
  dashboard reauthentication.
- Verify the returned seat principal before replacing credentials on the exact
  selected row, and reject mismatched browser identities without writing tokens.
- Clear persistent sticky and HTTP bridge bindings when an account becomes
  unusable because authentication was permanently revoked.

## Capabilities

### Modified Capabilities

- `frontend-architecture`: reauthentication targets and verifies one local seat
  instead of merging every row that shares a workspace identity.
- `sticky-session-operations`: accounts that become unusable clear persistent
  sticky and durable HTTP bridge continuity bindings.

## Impact

- OAuth start requests and server-side flow state
- Account token persistence and schema
- Account status cleanup for persistent affinity records
- Accounts dashboard reauthentication behavior

## Out of Scope

- Preventing every upstream OAuth token revocation discussed in #1085
- Changing the workspace account ID sent in upstream Codex request headers
