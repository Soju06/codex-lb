## Why

An unpinned file finalization polls the upstream on one account. If a later poll fails before dispatch, per-request transport provenance permits replaying the whole finalization on another account despite the earlier poll having reached the first account.

## What changes

- Deny cross-account replay of a routed file-finalize operation once an upstream poll has returned.
- Preserve pre-dispatch failover before its first poll, strict owner routing, and direct transport behavior.

## Scope

`app/core/clients/files.py`, the file-route regression tests, and the owning Responses API compatibility requirement. No new setting, retry loop, or wire contract.
