# responses-api-compat Delta

## ADDED Requirements

### Requirement: HTTP bridge accepted replays keep a hard-capable Codex session owner eligible

When the HTTP responses session bridge replays an accepted, output-free native Codex turn within its single response lifecycle (the `replay_downstream_response_id` capture of the accepted output-free capacity replay) on a hard bridge session key (`session_header`, `thread_header`, or `turn_state_header`), and the session affinity the reconnect selects with may resolve a hard `CODEX_SESSION` owner the request state does not carry -- a `CODEX_SESSION` affinity (bare session header or turn state) or any affinity that consults a raw legacy compatibility row (`legacy_selection_key`) -- the bridge MUST NOT exclude the account that accepted the turn from the replacement selection. The replay MUST reconnect through selection without that exclusion, so a resolved hard row resolves to the same owner again and the request is re-sent to it, and the reconnect MUST NOT wait on `hard_affinity_saturated` for the owner the replay itself excluded. The replacement reconnect MUST otherwise follow the established fresh hard-request path: the owner's account-scoped response-create lease is released and re-acquired for the selected account, no owner pin is installed, and the retry does not require a same-account reconnect, so a soft namespaced row may still move the replay through selection when the owner is unavailable. When that selection returns a different account, the replacement handshake MUST carry no turn state learned on the owner's socket (the modified requirement "Cross-account bridge retries clear turn-state" below applies to this unexcluded move as well).

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

## MODIFIED Requirements

### Requirement: Cross-account bridge retries clear turn-state

When an HTTP bridge request is replayed or reconnected on an account other than the one that served its retired socket -- whether that account was excluded before the reconnect (a pre-visible request proven safe to replay on another account) or the reconnect selected a different account without an exclusion (an accepted replay left unexcluded for a hard-capable Codex session owner whose soft namespaced row moved because the owner was unselectable) -- the proxy MUST NOT carry a turn state learned on the retired account into the replacement handshake. The replacement connection's `x-codex-turn-state` header, if any, MUST NOT be one learned from the retired account, and the proxy MUST clear the retired account's upstream and downstream turn-state from the session. The handshake decides this from the account selection actually returned, not from whether the retired account was excluded. A reconnect that returns to the same account keeps offering that account its own retained turn state.

#### Scenario: safe bridge replay excludes the stalled account

- **GIVEN** a pre-visible HTTP bridge request is proven safe to replay
- **WHEN** the failed bridge account is excluded before reconnect
- **THEN** the proxy clears the retired account's turn-state fields and header
- **AND** the replacement account receives no turn-state from the retired socket

#### Scenario: Unexcluded accepted replay moved by a soft row opens the replacement without the owner's turn state

- **GIVEN** a native Codex bridge session on a hard `session_header` key whose accepted output-free replay left its owner unexcluded
- **AND** the owner's socket issued an upstream turn state on its handshake
- **AND** no raw legacy hard row exists for the bare session header, so the owner is only the soft-row preference and is unselectable at reconnect time
- **WHEN** the reconnect selection returns the other account
- **THEN** the replacement handshake carries no `x-codex-turn-state`
- **AND** the session retains no turn state learned on the owner
- **AND** the request is re-sent once to the replacement account within the single lifecycle the client is reading

#### Scenario: Same-account reconnect keeps the retained turn state

- **GIVEN** a bridge session that retained the upstream turn state issued on its current account's socket
- **WHEN** the reconnect selection returns that same account
- **THEN** the replacement handshake carries that retained turn state, unchanged from established reconnect behaviour
