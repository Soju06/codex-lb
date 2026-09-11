# responses-api-compat Delta

## REMOVED Requirements

### Requirement: Operation-fenced hard turns wait through cooldown within the client retry budget

**Reason**: The requirement is explicitly conditional on "an explicit server recovery mode is enabled". `http_responses_session_bridge_ambiguous_continuation_recovery_mode` is deleted and `fail_closed` is the only behaviour, so the cooldown wait can never be entered. Its own "Default mode remains fail closed" scenario is what the code now does unconditionally, and that outcome is already covered by the retry-circuit cooldown requirements (a continuity-bound hard request during cooldown receives the immediate bounded cooldown failure with its retry hint).

**Migration**: None for operators running the shipped default. A deployment that had set `server_anchored_replay_once` or `server_indefinite_recovery` no longer holds a hard turn-state request through the retry-circuit cooldown; it receives the standard cooldown failure with `Retry-After` instead.

### Requirement: Fresh indefinite-recovery spool

**Reason**: The requirement bounds "dispatching a server-owned retry for a nonterminal operation". Server-owned retries were only dispatched by the `server_anchored_replay_once` / `server_indefinite_recovery` claim path, which is deleted; no code path clears an operation's partial spool for a retry any more.

**Migration**: None. The durable primitive that implemented the atomic clear (`DurableBridgeRepository.claim_unknown_operation_for_recovery`) is now unreferenced by the request path and keeps its repository-level tests until a follow-up retires it together with the `recovery_dispatch_count` column semantics.

### Requirement: Anchored indefinite recovery gate

**Reason**: The gate decided when to install the `server_indefinite_recovery` loop. The loop is deleted, so there is nothing to gate: an eventless anchored continuation and a fresh first turn both terminate through the normal error path.

**Migration**: None for operators running the shipped default. Under `server_indefinite_recovery` an eventless anchored continuation was held open across up to six server-owned retries; it now returns its terminal `response.failed` on the first failure like every other request.

### Requirement: Retry reservation terminalization

**Reason**: The requirement described how a *recovery attempt* that could not reacquire its API-key usage reservation had to settle the prior reservation and emit a terminal `response.failed`. There are no recovery attempts left; the single stream's reservation settlement and terminal event emission are covered by the existing reservation-settlement and terminal-delivery requirements.

**Migration**: None.

### Requirement: Retry output stops indefinite recovery

**Reason**: The requirement stopped the indefinite recovery loop once a retry attempt emitted a downstream event. The loop is deleted, so no second attempt can ever be appended to a stream.

**Migration**: None.

### Requirement: Fence same-session active operations

**Reason**: The requirement constrained when server-indefinite recovery could reset and redispatch a nonterminal operation; the `same_operation_pending` check it describes existed only inside the deleted recovery-claim branch. With that branch gone, every already-recorded operation is fail-closed unconditionally — submitted, acknowledged and `unknown` alike — so there is no "may enter a fresh recovery attempt" case left to fence.

**Migration**: None for operators running the shipped default. An `unknown` operation that a non-default mode could previously redispatch now always returns the existing `upstream_operation_status_unknown` 503 with a cooldown `Retry-After`.

### Requirement: Eventless server-owned bridge recovery is bounded

**Reason**: Its first paragraph MUSTs the deleted server-owned recovery loop and names `HTTP_BRIDGE_SERVER_RECOVERY_MAX_ATTEMPTS`, a constant this change deletes (zero remaining hits in `app/` and `tests/`), and its only scenario ("Exhausted eventless recovery terminates with one response id") drives that loop to exhaustion. A `MODIFIED` block cannot express this: the scenario has to go, and the requirement's own title states the deleted bound. Replaced by "Eventless bridge failures terminate with a stable response id" below, which keeps the still-live second paragraph verbatim.

**Migration**: None. The `response.id` guarantee on the eventless terminal event is unchanged and is now stated without the recovery bound; the retry cap simply no longer exists, so the first failure is the terminal one.

## ADDED Requirements

### Requirement: Eventless bridge failures terminate with a stable response id

When an anchored HTTP bridge continuation fails before any downstream response
event, the proxy MUST emit one terminal `response.failed` event.

That terminal event MUST include a stable `response.id` even when upstream
never emitted `response.created` or another response envelope before the
failure. Public `/v1/responses` normalization depends on that envelope to
synthesize the required leading `response.created` event without producing an
SDK parser failure.

#### Scenario: Eventless failure terminates with one response id

- **GIVEN** an anchored HTTP bridge continuation
- **AND** its upstream attempt fails before any downstream `response.*` event
- **WHEN** the bridge settles the turn
- **THEN** it emits one terminal `response.failed` event
- **AND** that terminal event includes a stable `response.id`
