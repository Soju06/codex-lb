## Why

Scheduled retry-circuit cleanup can delete a replay generation or failure that changed after its stale-row selection. Issue #2270 needs a fix on current main independent of the leased-receipt protocol and held policy decisions in #1954/#2271.

## What Changes

- Match each scheduled deletion against the selected timestamp, admission generation and failure count.
- Stop the cleanup pass after a conditional-delete miss, leaving changed rows to a later scheduled pass.
- Preserve existing retention and continuity rules and verify real repository races.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: scheduled retry-circuit cleanup preserves changes made after selection.

## Impact

Only the scheduled cleanup repository method, regression coverage and OpenSpec records change. Existing columns suffice; no schema, settings, receipt lease or runtime dependency on #1954 is introduced. The receipt lifecycle and its two held policy decisions stay together in #2271. An already-claimed row remains subject to main's existing retention grace; this does not promise receipt-based lifetime protection.
