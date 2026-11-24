## Why

Operators can pause accounts and delete sticky mappings but cannot inspect the
logged affinity in request details or explicitly choose a soft mapping's next
account. This makes ordinary policy preferences look like stuck routing and
encourages unsafe deletion of hard continuation ownership.

## What Changes

- Show historical request account and affinity in the request-details UI.
- Add a compare-and-set account switch for non-reserved prompt-cache mappings.
- Expose stable account IDs in sticky-session listing and a target picker.
- Explain configured strategy and per-account policy in the Accounts UI.
- Reuse Pause/Resume without deleting credentials or conversation history.

## Capabilities

### Modified Capabilities

- `sticky-session-operations`: guarded soft-affinity reassignment.
- `frontend-architecture`: historical routing visibility and operator controls.

## Impact

Dashboard/API changes only; no schema or new configuration. Hard continuation
ownership and requests already in flight are unaffected by soft reassignment.
