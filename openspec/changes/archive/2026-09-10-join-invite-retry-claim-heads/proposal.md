## Why

Main561311ded1d3191cf1ef271d4cd8ea97f8fd17a4 appends dashboard-user invites after audit030000. Combining it with the published200000 account/retry join leaves two heads for the public upgrade CLI.

## What Changes

Append a schema-neutral join between200000 and invites040000. Preserve every published migration and the incoming invite/account contract. Cover populated invite and account/retry parents, including exact merge-only downgrade preservation.

## Impact

Both deployed parent histories converge on one head without losing role, user/session, audit, invite or receipt data. Existing policy and live gates remain open.
