# responses-api-compat Delta

## ADDED Requirements

### Requirement: Accepted output-free capacity failures are replayed within a single response lifecycle

When a native Codex HTTP bridge or direct WebSocket `response.create` has been accepted upstream — `response.created` and optionally `response.in_progress` were forwarded downstream and no output item, text or tool delta, reasoning prelude, or tool call has been observed — and the turn then fails output-free, the proxy MUST re-send the request exactly once and the client MUST observe a single response lifecycle: exactly one `response.created`, no duplicated `response.in_progress`, and every later frame (including `response.completed` or a second terminal failure) carrying the response id the client already read. The bounded clean-close retry that pre-created requests receive MUST NOT extend an accepted lifecycle to a third send.

An output-free failure is either a terminal `error` / `response.failed` whose normalized code is `server_is_overloaded`, `overloaded_error`, or `model_at_capacity`, or whose message names the selected-model capacity, or a transport close that is not account-neutral. The terminal MUST NOT name another response and MUST NOT report output items or billed output or reasoning tokens. Quota and rate-limit codes after acceptance MUST keep their stronger classification and MUST NOT be replayed. Anchored continuations without a retry-safe fresh payload, requests sharing the socket with another pending request, and requests whose replay budget is consumed MUST NOT be replayed. The other-pending check MUST be evaluated under the pending lock at the moment the replay is decided, after every await the terminal handling performs, never from a snapshot taken before such an await. The requirement "Direct WebSocket replay never mixes numeric response sequences" is unchanged: a direct WebSocket request whose forwarded prelude carried a finite integer `sequence_number` MUST NOT be replayed, and its capacity terminal or transport close keeps the existing fail-closed handling.

The replay MUST capture the client-visible response id and arm prelude suppression before the request's upstream response id is cleared. On the HTTP bridge the replay MUST re-claim the session response-create gate without waiting; when another request holds the gate the upstream terminal MUST be forwarded unchanged. The replay MUST re-acquire shared work admission before sending, and when the request body is account-neutral the failing account MUST be excluded from the replacement selection on the HTTP bridge and on a direct WebSocket whose affinity cannot resolve to a hard sticky owner. A replay that swaps a retry-safe fresh body in for an anchored one MUST re-derive its owner requirement from the fresh body (an account-neutral body releases the anchor owner's pin; an account-bound body keeps it), and the failing account MUST NOT be excluded while the replay is still required to reconnect to it. On the direct WebSocket surface the failing account MUST NOT be excluded either when the request's affinity may resolve to a hard `CODEX_SESSION` owner the request state does not carry -- a `CODEX_SESSION` affinity (bare session header or turn state) or any affinity that consults a raw legacy compatibility row (`legacy_selection_key`) -- because a resolved hard row narrows selection to its owner and excluding that owner fails every re-selection with `hard_affinity_saturated`; such a replay reconnects through selection without an exclusion, exactly as the created-only transport-close replay did before this change. When the fresh body cannot release that pin -- the client supplied the anchor, or the fresh body names an account-scoped upload -- a capacity terminal MUST re-send the anchored body to the account that accepted it and MUST NOT fail the turn closed as `previous_response_owner_unavailable`. The classified capacity code an accepted terminal is replayed under MUST be a transparent replay code (`model_at_capacity` is reported as `server_is_overloaded`). When the request is API-key-backed, the failing account's health write MUST wait for the request's reservation settlement, as for pre-created replays. On the HTTP bridge only a terminal transport message (close or error) MAY replay an accepted turn.

#### Scenario: Bridge terminal capacity error after acceptance is retried on another account

- **GIVEN** the HTTP responses session bridge is enabled and two accounts are selectable
- **AND** upstream delivers `response.created` and `response.in_progress` for a native Codex request and then an `error` with `code = "server_is_overloaded"` or `code = "model_at_capacity"` and no output
- **WHEN** the bridge processes that terminal
- **THEN** the request is re-sent once on the other account
- **AND** the client observes exactly one `response.created`
- **AND** the `response.completed` the client receives carries that `response.created` id

#### Scenario: Bridge bare overload code after an accepted anchored follow-up is replayed

- **GIVEN** an HTTP bridge follow-up turn whose `previous_response_id` the proxy injected and whose full resend is retained as a retry-safe fresh body
- **AND** upstream accepted it (`response.created` forwarded) and produced no output
- **WHEN** upstream then emits an `error` with code `server_is_overloaded` or `overloaded_error` whose message does not name the selected-model capacity
- **THEN** the bridge stages the single-lifecycle replay and hands the request to the pre-created retry exactly as it does after the selected-model capacity message (owner-switch prep with the fresh body, or the anchored body to its owner)
- **AND** a client-supplied anchor is forwarded unchanged, because the bridge's pre-created retry only re-sends proxy-injected anchors

#### Scenario: Bridge abrupt close after acceptance is retried on another account

- **GIVEN** an unanchored native Codex bridge request whose `response.created` and `response.in_progress` were forwarded
- **WHEN** the upstream websocket closes with a non-account-neutral close before any output
- **THEN** the request is re-sent once on another account within the same single response lifecycle

#### Scenario: WebSocket accepted capacity failures are retried within one lifecycle

- **GIVEN** a direct `/backend-api/codex/responses` WebSocket request whose `response.created` and `response.in_progress` were forwarded
- **AND** the connection carries no Codex session affinity that may resolve to a hard sticky owner (no `CODEX_SESSION` kind and no raw legacy compatibility lookup)
- **WHEN** upstream then emits an output-free capacity `error` or closes the transport abruptly
- **THEN** the proxy reconnects excluding the failing account and re-sends the request once
- **AND** the client observes exactly one `response.created` and one `response.in_progress` and a `response.completed` carrying that id

#### Scenario: A sequenced direct WebSocket prelude keeps the existing fail-closed contract

- **GIVEN** a direct `/backend-api/codex/responses` WebSocket request whose forwarded `response.created` (0) and `response.in_progress` (1) carried finite integer `sequence_number` values
- **WHEN** upstream then emits an output-free capacity `error`
- **THEN** the proxy MUST NOT reconnect or re-send the request and MUST finalize and surface that terminal unchanged
- **WHEN** upstream instead closes the transport abruptly before any output
- **THEN** the proxy MUST record the request as `stream_incomplete` without emitting a synthetic terminal under the visible id and MUST close the downstream WebSocket with code 1011
- **AND** in both cases no replacement account is connected

#### Scenario: Output before the capacity failure disables the replay

- **WHEN** any `response.output_item.added`, text or tool delta, or buffered reasoning prelude was observed before the capacity terminal or transport close
- **THEN** the proxy MUST NOT replay the request and MUST forward the terminal unchanged

#### Scenario: Terminals reporting output are not replayed

- **WHEN** the capacity terminal payload carries a non-empty `output` list, `usage.output_tokens > 0`, or `usage.output_tokens_details.reasoning_tokens > 0`
- **THEN** the proxy MUST NOT replay the request

#### Scenario: Quota and rate-limit codes after acceptance stay fail-closed

- **WHEN** an accepted request fails with `rate_limit_exceeded`, `usage_limit_reached`, `insufficient_quota`, `usage_not_included`, or `quota_exceeded`, even with the selected-model capacity message
- **THEN** the proxy MUST forward the terminal without replaying

#### Scenario: Anchored continuations without a retry-safe fresh payload are not replayed

- **WHEN** the accepted request carries `previous_response_id` and no retry-safe fresh payload is retained
- **THEN** the proxy MUST forward the failure without replaying

#### Scenario: A second capacity failure surfaces one terminal under the visible id

- **WHEN** the replayed request also fails output-free, including a clean upstream close before the replay's `response.created`
- **THEN** the proxy MUST NOT attempt a third send
- **AND** the client observes one terminal failure with no second `response.created` or `response.in_progress`

#### Scenario: An anchored accepted follow-up is replayed with its fresh body on another account

- **GIVEN** a direct WebSocket follow-up turn whose `previous_response_id` the proxy injected and whose full resend is retained as a retry-safe, account-neutral fresh body
- **AND** the connection carries no Codex session affinity that may resolve to a hard sticky owner
- **AND** upstream accepted the turn (`response.created` and `response.in_progress` forwarded) on the anchor's owner
- **WHEN** upstream then emits an output-free capacity `error` with code `server_is_overloaded` or `model_at_capacity`, or closes the transport abruptly
- **THEN** the proxy re-sends the fresh body without `previous_response_id` on another account, excluding the owner
- **AND** the client observes exactly one `response.created` and a `response.completed` carrying that id

#### Scenario: An account-bound accepted replay reconnects to its owner

- **WHEN** an accepted replay's body still requires one account (bound replay owner, uploaded file, anchored owner, or turn-state owner)
- **THEN** the proxy MUST NOT exclude that account and MUST reconnect to it

#### Scenario: A Codex-session accepted replay keeps its hard sticky owner eligible

- **GIVEN** a direct WebSocket connection whose `session_id` (or thread) header selects a `CODEX_SESSION` affinity, so selection consults the raw legacy compatibility row for that key
- **AND** that raw row names one account as the hard owner while the request state carries no owner pin (unanchored, account-neutral turn)
- **AND** upstream accepted the turn on that owner (`response.created` and `response.in_progress` forwarded) and then emitted an output-free capacity `error` or closed the transport abruptly
- **WHEN** the proxy replays the turn
- **THEN** the proxy MUST NOT exclude the owner or request a sticky reallocation, and MUST reconnect through selection so the hard row resolves to the owner again
- **AND** the client observes exactly one `response.created` and a `response.completed` carrying that id, never a connect failure after `hard_affinity_saturated`
- **AND** the owner still receives the capacity health penalty (deferred behind API-key settlement when the request is keyed)
- **AND** a request whose affinity cannot resolve to a hard owner (no `CODEX_SESSION` kind, no raw legacy lookup) is still excluded and moved

#### Scenario: A transport close of a client-anchored accepted turn reconnects to its owner

- **GIVEN** a direct WebSocket accepted turn (`response.created` and `response.in_progress` forwarded) whose `previous_response_id` the client supplied and whose full resend is retained as a retry-safe fresh body
- **WHEN** upstream closes the transport abruptly before any output
- **THEN** the proxy re-sends the fresh body once to the account that accepted it, keeping the owner pin the client's anchor established and without excluding that account
- **AND** the client observes exactly one `response.created` and a `response.completed` carrying that id

#### Scenario: A turn-state session re-sends an accepted replay to its owner

- **GIVEN** a direct WebSocket connection whose `x-codex-turn-state` resolves to an owner account (the native Codex flow: the handshake token of the previous connection is echoed and the proxy injects the completed id as `previous_response_id`, retaining the full resend as a retry-safe fresh body)
- **AND** upstream accepted the follow-up on that owner and then emitted an output-free capacity `error` or closed the transport abruptly
- **WHEN** the proxy replays the turn with the fresh body
- **THEN** the turn-state owner pin survives the fresh-body install (it is a session pin, not a body pin) and the proxy MUST NOT exclude the owner
- **AND** the proxy reconnects to the owner on a fresh socket and re-sends the fresh body once
- **AND** the client observes exactly one `response.created` and a `response.completed` carrying that id, never `previous_response_owner_unavailable`
- **AND** a pre-created owner replay in the same session (capacity code before `response.created`) is likewise re-sent to the owner instead of excluding it

#### Scenario: A capacity terminal of an anchored accepted turn that cannot leave its owner is re-sent to that owner

- **GIVEN** a direct WebSocket accepted turn (`response.created` and `response.in_progress` forwarded) that carries `previous_response_id` and retains a retry-safe fresh body
- **AND** the fresh body cannot release the anchor owner's pin: the client supplied the anchor, or the fresh body names an account-scoped uploaded file
- **WHEN** upstream emits an output-free capacity `error`
- **THEN** the proxy re-sends the anchored body once to the account that accepted it, without excluding it
- **AND** the client observes exactly one `response.created` and a `response.completed` carrying that id
- **AND** the proxy MUST NOT rewrite the terminal into `previous_response_owner_unavailable`
- **AND** for an API-key-backed request the owner's health write waits for the reservation settlement

#### Scenario: Accepted replay health writes wait for API-key settlement

- **GIVEN** an API-key-backed accepted request that fails output-free and is replayed
- **WHEN** the replay reaches its terminal
- **THEN** the failing account's health write is applied only after the request's reservation settlement commits

#### Scenario: Another pending request or a busy create gate forwards the original error

- **WHEN** another request is pending on the same upstream socket, or another `response.create` holds the bridge session response-create gate
- **THEN** the proxy MUST forward the upstream terminal unchanged and MUST NOT modify the accepted request's identity

#### Scenario: A younger turn admitted while the accepted terminal is handled forwards the original error

- **GIVEN** a direct WebSocket accepted request (`response.created` and `response.in_progress` forwarded) that released the session response-create gate at `response.created`
- **AND** the sender admits and sends a younger `response.create` on the same upstream socket while the reader awaits the accepted request's thread-affinity refresh for its output-free capacity terminal
- **WHEN** the reader decides whether to replay the accepted request
- **THEN** the other-pending guard MUST observe the younger request
- **AND** the proxy MUST forward the upstream terminal unchanged, MUST NOT modify the accepted request's identity, and MUST NOT retire the shared socket under the younger request

#### Scenario: A transport close while another response shares the bridge socket replays nothing

- **GIVEN** an HTTP bridge upstream socket carrying an accepted, output-free request and a sibling: either a response the client is already reading, or a pre-created `response.create` that has not seen its `response.created` yet and still holds the session response-create gate
- **WHEN** the upstream socket closes abruptly
- **THEN** the proxy MUST NOT reconnect the accepted request alone, and MUST NOT reconnect the pre-created sibling alone either
- **AND** both pending requests fail closed with `stream_incomplete` promptly
- **AND** for the visible-sibling shape this is exactly what happened before accepted replays existed; for the pre-created-sibling shape it replaces the earlier behaviour of retrying the pre-created sibling alone while the accepted request stayed bound to the dead upstream until the stale pending sweep -- the pre-created sibling gives up its lone retry so no request is stranded

#### Scenario: A binary frame does not replay an accepted turn

- **WHEN** the bridge upstream socket yields a protocol-invalid binary frame while an accepted request is pending
- **THEN** the proxy MUST NOT replay the accepted request

## MODIFIED Requirements

### Requirement: HTTP bridge model-capacity retry waits preserve stream contracts

The proxy MUST wait before replaying an HTTP bridge request with a selected-model capacity failure only when the
failure happened before any downstream-visible model output and the request is still replayable as a fresh request.
An already-forwarded `response.created` or `response.in_progress` does not disqualify the replay; the replay's
duplicate lifecycle prelude MUST be suppressed so the client observes exactly one `response.created`.

#### Scenario: Public propagated-error streams do not receive pre-retry keepalives

- **WHEN** a `/v1/responses`-compatible HTTP bridge stream is configured to propagate startup HTTP errors
- **AND** upstream returns a selected-model capacity error before `response.created`
- **THEN** the proxy MUST NOT emit `codex.keepalive` or account-capacity wait events before the retry completes.

#### Scenario: Accepted public streams are replayed without keepalives

- **WHEN** a `/v1/responses`-compatible HTTP bridge stream has already forwarded `response.created`
- **AND** upstream fails the response output-free with a selected-model capacity error
- **THEN** the proxy MUST replay the request within the single lifecycle without emitting `codex.keepalive` frames
- **AND** the proxy MUST NOT re-signal the pre-response startup wait for that request.

#### Scenario: Replay waits remain bounded by the original bridge deadline

- **WHEN** the selected-model capacity error arrives near or after the original bridge request deadline
- **THEN** the proxy MUST NOT start a fresh upstream replay after that deadline is exhausted.

#### Scenario: Only fresh replayable bridge requests wait

- **WHEN** the selected-model capacity error belongs to an anchored request that cannot be replayed without
  `previous_response_id`
- **THEN** the proxy MUST forward the terminal error promptly without sleeping for the model-capacity retry delay.

#### Scenario: Retry-safe injected anchors still wait

- **WHEN** the proxy injected `previous_response_id` and retained a fresh request body that is safe to replay without
  that anchor
- **AND** upstream returns a selected-model capacity error before visible output
- **THEN** the proxy MUST apply the model-capacity wait before stripping the injected anchor and replaying the fresh
  request.

#### Scenario: Accepted client-anchored requests forward the capacity message without waiting

- **WHEN** an accepted request (`response.created` forwarded) carries a `previous_response_id` the client supplied,
  even with a retry-safe fresh body retained
- **AND** upstream returns a selected-model capacity error before visible output
- **THEN** the proxy MUST NOT reserve, stage, or wait for that request
- **AND** the upstream terminal MUST be forwarded unchanged, exactly as the bare transparent-code branch forwards it.

#### Scenario: Remote-owner relay preserves the hidden startup wait

- **WHEN** an origin replica forwards a bridge request to its remote owner
- **THEN** the origin MUST keep its startup probe pending until the owner relay returns response headers or a terminal
  startup error
- **AND** a selected-model capacity wait on the owner MUST NOT cause the origin to commit HTTP 200 before that wait
  completes.

#### Scenario: Waiting keeps the retry tied to the pending request

- **WHEN** the proxy waits before replaying a selected-model capacity failure
- **THEN** the request MUST remain reserved in the bridge pending queue while it waits
- **AND** the proxy MUST retain the session response-create gate so a younger request cannot enter while the sole
  upstream reader is sleeping
- **AND** the proxy MUST release account-level and shared response-create capacity during the wait
- **AND** the proxy MUST reacquire both capacity leases before sending the replay
- **AND** the proxy MUST skip the replay if that queued request detaches before the wait completes
- **AND** when the wait branch gives that pending ownership up without a successful replay (the replay was refused or
  failed, or the session gate could not be re-claimed) it MUST record the request's terminal settlement claim, so an
  abort before finalization still settles the API-key reservation through the shielded abort settlement instead of
  orphaning the reservation, its heartbeat, and the re-claimed session gate.

#### Scenario: Accepted requests re-claim the session gate before waiting

- **WHEN** the selected-model capacity failure belongs to a request that already forwarded `response.created`
- **THEN** the proxy MUST re-claim the session response-create gate without waiting before it waits and replays
- **AND** if another `response.create` holds that gate the proxy MUST forward the upstream terminal unchanged.
