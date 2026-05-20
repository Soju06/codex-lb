# Mask Unmatched Previous-Response Misses

## Why

Recent live request logs show codex-lb health is green while continuity-sensitive
traffic still sees owner_account_unavailable, stream_incomplete, and websocket
open timeout failures. The matched previous-response paths already rewrite raw
upstream previous_response_not_found errors, but an ambiguous or unmatched
upstream miss can still be handled as an unclassified reconnect with weak
operator diagnostics.

## What Changes

- Treat unmatched or ambiguous upstream previous_response_not_found events as
  fail-closed continuity decisions.
- Keep downstream/client errors on the retryable stream_incomplete shape when a
  pending request can be safely identified.
- Emit hashed, low-cardinality diagnostics for unmatched previous-response misses
  and owner-unavailable continuity masking.

## Impact

- Tightens websocket and HTTP bridge continuity behavior without changing normal
  success routing.
- Adds focused regression coverage for the ambiguous/unmatched event shape.
- Improves log evidence for account/owner continuity triage without exposing raw
  response ids.
