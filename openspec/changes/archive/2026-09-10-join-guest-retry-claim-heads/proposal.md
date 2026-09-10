## Why

Main d6a7ca662860e2b427e462f434319273bcd240bd adds a guest-session generation migration branching from spool retention. Combining it with the published receipt/spool merge leaves two heads and conflicts in an older migration test.

## What Changes

Preserve both histories and join the guest-generation and published receipt/spool heads with a new schema-neutral merge. Reconcile the historical test assertions to preserve existing fields and verify the incoming guest-generation default.

## Impact

The public head upgrade converges from either populated parent, preserving live receipts, spool retention and guest-session generations. Guest authorization and receipt lifecycle policy remain unchanged.
