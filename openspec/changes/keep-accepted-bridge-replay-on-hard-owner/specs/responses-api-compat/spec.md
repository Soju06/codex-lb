# responses-api-compat Delta

## ADDED Requirements

### Requirement: HTTP bridge accepted replays keep a hard-capable Codex session owner eligible

When the HTTP responses session bridge replays an accepted, output-free native Codex turn within its single response lifecycle (the `replay_downstream_response_id` capture of the accepted output-free capacity replay) on a hard bridge session key (`session_header`, `thread_header`, or `turn_state_header`), and the session affinity the reconnect selects with may resolve a hard `CODEX_SESSION` owner the request state does not carry -- a `CODEX_SESSION` affinity (bare session header or turn state) or any affinity that consults a raw legacy compatibility row (`legacy_selection_key`) -- the bridge MUST NOT exclude the account that accepted the turn from the replacement selection. The replay MUST reconnect through selection without that exclusion, so a resolved hard row resolves to the same owner again and the request is re-sent to it, and the reconnect MUST NOT wait on `hard_affinity_saturated` for the owner the replay itself excluded. The replacement reconnect MUST otherwise follow the established fresh hard-request path: the owner's account-scoped response-create lease is released and re-acquired for the selected account, no owner pin is installed, and the retry does not require a same-account reconnect, so a soft namespaced row may still move the replay through selection when the owner is unavailable.

The created-only (pre-created) bridge replay MUST keep excluding the silent account exactly as before; a soft bridge session key MUST keep excluding the failing account so the accepted replay moves to another account; a model-fallback replay MUST keep excluding the rejecting account; and a hard session key whose affinity cannot resolve a hard owner MUST keep the exclusion. The predicate deciding whether an affinity may resolve a hard owner MUST be shared with the direct WebSocket surface.

#### Scenario: Bridge bare-session accepted failure is re-sent to its hard sticky owner

- **GIVEN** the HTTP responses session bridge is enabled, two accounts are selectable, and a native Codex request carries a `session_id` header, so the bridge session key is hard (`session_header`) and its affinity consults the raw legacy `CODEX_SESSION` row for that value
- **AND** that raw row names the accepting account as the hard owner while the request carries no owner pin (unanchored, account-neutral turn)
- **AND** upstream delivers `response.created` and `response.in_progress` on that owner and then an output-free capacity `error` (`server_is_overloaded` or `model_at_capacity`) or closes the transport abruptly (1011 or 1006) before any output
- **WHEN** the bridge replays the turn
- **THEN** the replacement selection is performed with no excluded account and resolves the raw row to the same owner
- **AND** the request is re-sent once to that owner on a fresh socket and never to the other account
- **AND** the client observes exactly one `response.created` and a `response.completed` carrying that id, never a `hard_affinity_saturated` selection failure or a synthetic `stream_incomplete`

#### Scenario: Created-only and soft-key bridge replays keep excluding the failing account

- **GIVEN** the same hard `session_header` bridge session whose affinity consults the raw legacy row
- **WHEN** a pre-created request (no `response.created` observed) is replayed after a transport close
- **THEN** the silent account is excluded from the replacement selection exactly as before this change
- **WHEN** instead an accepted output-free request on a soft bridge session key (for example `request` or `prompt_cache`) is replayed
- **THEN** the failing account is excluded and the replay moves to another account, unchanged
