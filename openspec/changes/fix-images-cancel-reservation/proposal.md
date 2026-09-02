## Why

Limited-API-key image generation and edit requests can be cancelled while the
Images adapter is awaiting the first upstream SSE frame. That cancellation
currently bypasses the adapter's error callback, leaving its route-owned usage
reservation charged until stale reclamation instead of releasing it during the
request lifecycle.

## What Changes

- Release the Images route-owned API-key reservation when cancellation
  interrupts first-frame priming.
- Close the upstream iterator and complete reservation cleanup despite active
  cancellation, then propagate the original terminal unchanged.
- Keep cleanup failures diagnostic-only so they cannot replace the original
  cancellation.
- Add route-level regression coverage for both `/v1/images/generations` and
  `/v1/images/edits`, proving exactly-once release before the first upstream
  frame while preserving existing post-first-frame image settlement.
- Keep the existing `ProxyResponseError` branch, captured-token finalization,
  billing, stale-reclamation policy, and external response shapes unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: Require immediate, exactly-once cleanup of an Images route-owned
  limited-key reservation when cancellation interrupts upstream stream priming.

## Impact

The change is limited to first-frame priming in `app/modules/proxy/api.py`, the
Images integration regression surface, and the API-key reservation lifecycle
contract. It completes the pre-terminal cancellation path that was explicitly
outside the scope of PR #1822. It adds no API, setting, dependency, migration,
dashboard change, or alternate settlement owner.
