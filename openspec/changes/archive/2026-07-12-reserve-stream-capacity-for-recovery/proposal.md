## Why

Parallel first turns can consume every account-local stream slot. If an established turn then loses its upstream transport, its continuity-safe reattach competes with new work and can remain stuck even though reserving one existing slot would have let it recover.

## What Changes

- Reserve one account-local stream slot by default from ordinary first-turn and follow-up selection.
- Let reattach selection use the full configured account stream cap.
- Keep the reserve configurable and preserve the hard cap.

## Impact

New fan-out may reach local backpressure one slot earlier per account. Established responses retain capacity to recover after a transient upstream disconnect.
