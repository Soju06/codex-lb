# Repair CPA catalog readiness

## Why

PR #2305 fails legacy/bootstrap database upgrades and drops valid reasoning metadata. Refresh database failures can break catalog reads, and a stalled source blocks later acquisition batches. Mode changes retain selectable rows from the previous catalog owner. Nested tool aliases bypass normalization in CPA mode.

## What changes

- Make the unmerged catalog migration tolerate columns created by legacy/bootstrap paths.
- Preserve reasoning entries with absent descriptions, using their effort as the display description.
- Isolate refresh failures, include source enumeration in the acquisition budget, and reuse free acquisition slots without batch barriers.
- Disable prior catalog rows when changing modes without explicit replacement, retaining their routing ownership.
- Normalize allowed-tool aliases without dropping CPA tool declarations.
- Document HTTPS for remote inference credentials while retaining accepted local HTTP compatibility.

## Impact

The existing dashboard source API, public/Codex catalogs, Responses routes and migration CLI are the verification boundaries. No provider, deployment or frontend change is included. Fixes remain in the issue #2290 capability.
