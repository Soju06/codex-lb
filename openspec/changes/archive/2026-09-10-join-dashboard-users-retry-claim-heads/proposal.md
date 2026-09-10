## Why

Main8e5760726a34332d869aac682a3932170621966b extends guest-session history through dashboard roles, users, compat-admin credential projection and audit-actor columns. Combined with the published retry-claim merge, the public default upgrade has two heads.

## What Changes

Append a schema-neutral join between the published guest/retry merge and the latest account/audit migration. Preserve every published migration identifier, edge and body, including the incoming role/user/session behavior.

## Impact

Databases at either populated parent converge on one head while retaining role rows, grants, users, guest generations and receipt/spool state. No authorization, receipt lifetime, settlement or mixed-version policy is selected here.
