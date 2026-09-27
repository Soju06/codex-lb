## Why

An old Codex conversation fails with HTTP 409 after a tool result because the client-generated result item ID is treated as an unknown upstream reference. A bounded production diagnostic found nineteen references owned by one current source and one unknown function_call_output ID, despite that output's call_id being owned by the same source.

## What Changes

- Classify IDs on validated client-authored tool results as bookkeeping while retaining their call_id ownership requirement.
- Preserve unknown, conflicting, disabled or changed call owners and opaque state guards; retain the forwarded body and subscription replay behavior.
- Cover the failing native HTTP route, equivalent routes, multi-source expansion and cross-backend continuity with regression tests.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `model-source-routing`: distinguish client tool-result IDs from upstream call ownership.

## Impact

Direct Responses ownership extraction and strict replay-shape validation helpers; no new configuration, schema migration, external dependency or source reconfiguration. Roll out through the existing HA surge script to all serving replicas.
