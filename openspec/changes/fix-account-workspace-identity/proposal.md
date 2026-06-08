## Why

codex-lb currently treats imported ChatGPT credentials as if email and plan were enough to identify an account. That is unsafe for modern ChatGPT Business/Enterprise workspaces: one login can belong to multiple workspaces, and seat type/plan are mutable attributes of a workspace membership. A free/personal usage-limit event must not overwrite or disable a same-email Business workspace credential.

## What Changes

- Add account workspace metadata (`workspace_id`, `workspace_label`, `seat_type`) and expose it in account summaries.
- Resolve imports and OAuth completion by workspace membership identity when available: upstream user id plus workspace id.
- Keep unknown-workspace credentials local-slot scoped instead of merging them by email.
- Guard usage refresh so mismatched workspace/plan payloads do not write usage, sync plan, or change status on the wrong account.
- Clarify settings/UI language around credential slots rather than duplicate emails.

## Impact

- Existing account ids remain stable.
- Existing credentials without workspace metadata keep working as local/legacy slots.
- Future tokens or usage payloads that expose workspace identity can safely update the matching slot.
