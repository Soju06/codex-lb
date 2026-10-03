## Decisions

- Hash the API-key/public-model/reference tuple before storage; never persist raw reference values in the ownership tables.
- Bind ownership to source ID, endpoint, effective upstream model and credential ciphertext. Preserve ciphertext when an operator resubmits the same decrypted credential.
- Commit reference evidence before client handoff; retain historical ownership beyond the active 30-day row TTL.
- Use the existing dispatch owner's cancellation-safe settlement and independent background database sessions.
- Reuse existing subscription replay predicates for classification, adding source-only helper paths without changing subscription behavior.

## Scope

This prerequisite contains no WebSocket transport, UI flag or subscription-overflow. It is a dependency of the native source WebSocket proposal. Rollback requires handling the new tables/column through the migration graph; old references cannot be assumed portable after evidence is removed.
