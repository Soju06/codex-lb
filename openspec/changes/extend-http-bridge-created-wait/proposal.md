# Extend HTTP bridge response-created wait

## Provenance
Local addition in the fix/python-image-bridge-consolidation branch
(helpers.py 100s cap); see
allow-bounded-inline-images-on-bridge/consolidation.md.

## Why
Visible HTTP bridge requests can exceed the existing 60-second acknowledgement cap, including small image-bearing requests. Permit 100 seconds without changing replay safety.

## What Changes
- Raise the shared pre-created watchdog cap from 60 to 100 seconds.
- Preserve a shorter stuck-gate threshold and all existing request budgets.
- Preserve eligibility, lifecycle re-anchoring, settlement, and ambiguous-image replay suppression.

## Impact
- Capability: proxy-admission-control.
- Local bridge restart required; no schema, credential, or environment changes.
