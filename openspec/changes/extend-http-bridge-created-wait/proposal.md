# Extend HTTP bridge response-created wait

## Why
Visible HTTP bridge requests can exceed the existing 60-second acknowledgement cap, including small image-bearing requests. Permit 100 seconds without changing replay safety.

## What Changes
- Raise the shared pre-created watchdog cap from 60 to 100 seconds.
- Preserve a shorter stuck-gate threshold and all existing request budgets.
- Preserve eligibility, lifecycle re-anchoring, settlement, and ambiguous-image replay suppression.

## Impact
- Capability: proxy-admission-control.
- Local bridge restart required; no schema, credential, or environment changes.
