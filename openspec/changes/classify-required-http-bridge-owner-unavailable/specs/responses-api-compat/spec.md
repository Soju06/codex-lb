## ADDED Requirements

### Requirement: Verified full resend can recover from selection-time owner loss

An HTTP bridge request MAY move from an unavailable continuity owner to another account only after a typed pre-visible `continuity_owner_unavailable` account-selection result, which the HTTP bridge maps to `previous_response_owner_unavailable`, and positive durable proof that the request contains the complete retained input history. A missing durable owner is not a selector result and MUST fail closed without replay. The durable row MUST provide a positive input-item count and full fingerprint, and the corresponding raw prefix of the incoming list-shaped input MUST match both before any projection occurs.

After the raw prefix proof, the service MUST construct a deterministic plaintext projection by omitting `reasoning`, `web_search_call`, `tool_search_call`, and `tool_search_output` items and removing upstream `id` fields from every retained input item. Retained `internal_chat_message_metadata_passthrough` MUST contain only a nonblank string `turn_id` when present. The projected suffix after the projected prefix MUST contain a completed assistant `output_text` or `refusal` boundary with nonblank content followed by nonblank fresh text or valid fresh file/image input. The suffix MAY contain multiple intervening turns only when every non-final user-input sequence is followed by another completed assistant boundary and the final sequence ends in fresh input. Direct intrinsic calls MAY precede an assistant boundary only when terminal completed or failed outputs settle every represented call in order. A call at the end of the verified raw prefix MAY be settled by its matching output at the start of the suffix. A direct-call/output sequence alone MUST NOT prove completeness because the persisted metadata does not identify omitted parallel calls. A matching prefix followed only by new user input, empty content, tool-call-only output, in-progress or partial retained output, duplicate, unmatched, or unresolved calls, or misordered call output MUST fail closed.

The service MUST validate the complete projected request after removing `previous_response_id`; it MUST reject nonblank conversation or prompt handles, remaining encrypted content, compaction, opaque account-scoped file/container/vector handles, nonportable file schemes, hosted, MCP, program-mediated, or unknown call or tool-choice state, unknown top-level fields, unknown or malformed top-level reasoning configuration, malformed message/content shapes, and tool outputs without exactly one matching intrinsic call. Assistant messages MUST contain only supported output parts, while user, system, and developer messages MUST contain only supported input parts. Inline data images and HTTP(S) file/image content MAY remain eligible. Eligible declared tools, tool choices, and retained direct calls MUST be shape-validated, account-neutral, and self-contained. Web-search filters, context size, and approximate location MUST use only the recognized nested fields and value types. An apply-patch call MUST use exactly one representation: a recognized structured `operation` with its exact discriminated fields, a nonblank legacy `patch`, or a nonblank legacy `input`.

For an eligible replay, the service MUST remove `previous_response_id`, strip every downstream session/turn alias, clear hard affinity, exclude the unavailable owner, prevent initial bridge-owner forwarding, and submit the complete projected request through a fresh server-namespaced recovery lane. It MUST NOT replay after downstream-visible output. Selection policy conflicts, authentication/connection failures after selection, incomplete history, or any unsafe request state MUST remain fail-closed.

#### Scenario: Client-supplied full resend moves from A to B

- **GIVEN** account A owns a completed previous response and its durable row stores the completed input count and fingerprint
- **AND** a follow-up supplies that previous response plus an account-neutral full resend whose retained prefix matches both values
- **WHEN** required-owner selection returns typed `continuity_owner_unavailable` before output
- **THEN** the bridge removes the previous-response anchor and all stale affinity headers
- **AND** excludes account A and submits the complete fresh request once on account B
- **AND** the next turn for the recovered task remains on account B

#### Scenario: Proxy-injected anchor protects an equivalent full resend

- **GIVEN** a hard durable alias resolves a completed response and the incoming full resend matches its retained count and fingerprint
- **AND** the proxy injects that response as the reattach anchor
- **WHEN** required-owner selection returns typed `continuity_owner_unavailable` before output
- **THEN** the same fresh-replay rules apply after the injected anchor is removed

#### Scenario: Verified resend contains owner-bound reasoning

- **GIVEN** a verified full resend contains encrypted reasoning, server-assigned item IDs, and completed web or tool-search bookkeeping
- **AND** its retained assistant and direct-tool content is otherwise complete and portable
- **WHEN** required-owner selection returns typed `continuity_owner_unavailable` before output
- **THEN** the bridge omits the reasoning and search bookkeeping and strips upstream item identities
- **AND** no encrypted content or upstream item identity is sent to account B
- **AND** the validated plaintext projection is submitted once on account B

#### Scenario: Retained request contains account-scoped state

- **GIVEN** a full resend contains a conversation or prompt handle, compaction, encrypted content outside an omitted reasoning item, an opaque account-scoped file/container/vector handle, a nonportable file scheme, hosted or MCP call or tool-choice state, an unknown call type, or an unmatched tool output
- **WHEN** its required owner is unavailable
- **THEN** the request fails with `previous_response_owner_unavailable`
- **AND** none of that state is sent to another account

#### Scenario: Request shape is not completely understood

- **GIVEN** a purported full resend contains an unknown top-level field or malformed/unknown message content
- **WHEN** its required owner is unavailable
- **THEN** replay eligibility fails closed
- **AND** the service does not infer portability from the retained fingerprint alone

#### Scenario: Matching input prefix omits the prior response output

- **GIVEN** the incoming input prefix matches the durable count and fingerprint
- **AND** the suffix contains only a new user message, a direct-call/output sequence without a later completed assistant boundary, partial retained output, or unresolved direct calls
- **WHEN** the required owner is unavailable
- **THEN** replay eligibility fails closed with `previous_response_owner_unavailable`
- **AND** the proxy does not drop the previous-response anchor or send the incomplete transcript to another account

#### Scenario: Owner was selected before a later failure

- **GIVEN** the required owner was selected successfully
- **WHEN** refresh, authentication, WebSocket connection, transport, or timeout fails before output
- **THEN** the request keeps that ordinary failure classification
- **AND** the service does not activate cross-account full-resend recovery

#### Scenario: Durable continuity row has no account owner

- **GIVEN** a durable continuity row proves retained input but has no account owner
- **WHEN** the request is evaluated before account selection
- **THEN** the bridge returns `previous_response_owner_unavailable`
- **AND** it does not treat the missing owner as a typed selector miss or replay on another account

#### Scenario: Failure occurs after visible output

- **WHEN** any part of a response has become downstream-visible
- **THEN** the service does not replay the request on another account
- **AND** it terminates through the existing partial-output failure contract

### Requirement: Verified replay continuity remains task-specific and fenced

A recovery lane MUST use a server-namespaced key within the existing durable `internal_unanchored_parallel` kind. Once it registers a turn-state or previous-response alias, that specific alias MUST resolve the recovery lane ahead of a conflicting broad session-header alias, while the shared session-header alias remains unchanged for sibling tasks. Conflicting specific aliases MUST still fail with `continuity_owner_conflict`, and unrelated internal lanes MUST NOT receive recovery precedence.

Alias ownership changes MUST be atomic. A recovery lane MAY replace only an alias owned by a documented prompt-cache, session-header, or turn-state predecessor, or by an ownerless or released/null-lease or lease-expired prior recovery lane. It MUST NOT replace an actively leased recovery lane. An ordinary or stale session MUST NOT replace a recovery alias. Owner-epoch fencing MUST invalidate the fenced local session; rejection because an alias is protected MUST remove only the rejected alias and MUST preserve sibling aliases and the rest of the session.

A recovery lane MUST acquire and renew fenced durable ownership before publishing continuity or dispatching a request. Immediately before upstream dispatch, the lane MUST atomically publish the incoming turn-state alias and its latest-turn state. If cancellation occurs after that commit but before dispatch may have started, cleanup MUST roll back the provisional alias and latest-turn state before reporting cancellation. Rollback MAY restore the predecessor only when its owner epoch and account still match the registration receipt; otherwise rollback MUST remove only the provisional alias. Once dispatch may have started, the alias MUST remain on the recovery lane and the socket MUST retire rather than making an ambiguous resend possible. A completed response MUST NOT be advertised downstream as successful when its required durable response-alias publication fails.

#### Scenario: Recovered task and sibling share a session header

- **GIVEN** a recovered task registered a specific alias on account B
- **AND** its shared session header still resolves a sibling lane on account A
- **WHEN** the recovered task sends both aliases
- **THEN** durable lookup resolves account B's recovery lane
- **AND** session-header-only sibling traffic continues to resolve account A

#### Scenario: Specific recovery aliases conflict

- **GIVEN** a recovery turn-state alias and previous-response alias resolve different durable sessions
- **WHEN** both are supplied
- **THEN** the request fails with `continuity_owner_conflict`
- **AND** broad-alias precedence does not hide the conflict

#### Scenario: Stale session attempts to reclaim a recovery alias

- **GIVEN** a recovery lane owns a specific durable alias
- **WHEN** an ordinary or stale predecessor session registers the same alias
- **THEN** the durable write reports the alias as protected
- **AND** the recovery alias remains unchanged
- **AND** unrelated aliases on the rejected session remain usable

#### Scenario: Recovery lane rebinds a documented predecessor alias

- **GIVEN** a prompt-cache, session-header, turn-state, or ownerless/lease-expired prior recovery lane owns an alias for the same recovered task
- **WHEN** the current recovery lane registers that alias while its owner epoch is valid
- **THEN** the conditional durable write rebinds the alias atomically

#### Scenario: Active recovery lane protects its alias

- **GIVEN** an actively leased recovery lane owns a specific alias
- **WHEN** another recovery lane attempts to register that alias
- **THEN** the durable write reports the alias as protected
- **AND** the active recovery owner and alias remain unchanged

#### Scenario: Cancellation occurs after alias commit and before dispatch

- **GIVEN** a recovery lane atomically committed the incoming turn-state alias immediately before dispatch
- **WHEN** its task is cancelled before upstream send may have started
- **THEN** cleanup completes the fenced rollback before surfacing cancellation
- **AND** the predecessor is restored only if its captured epoch and account are unchanged
- **AND** otherwise only the provisional recovery alias is removed
- **AND** no upstream request is sent

#### Scenario: Cancellation occurs after dispatch may have started

- **GIVEN** a recovery lane committed the incoming turn-state alias
- **WHEN** cancellation occurs after upstream send may have started
- **THEN** the recovery alias remains authoritative
- **AND** the ambiguous socket retires before an admitted waiter can reconnect or submit on it

#### Scenario: Completed response alias cannot be persisted

- **WHEN** a recovery response reaches `response.completed`
- **AND** fenced durable publication of its response alias fails
- **THEN** downstream does not receive a successful completion for that response
- **AND** the recovery lane retires fail-closed

### Requirement: Recovery provenance survives lifecycle transitions

Reconnect, prewarm, authorization retry, and model-transition paths for a recovery lane MUST require its current account and MUST preserve typed ownership provenance. A model-transition descendant MUST receive a fresh server-namespaced recovery key. Every fresh connection to the replacement account MUST omit stale downstream affinity headers.

When a recovery request is later forwarded to its current replica owner, the origin MUST remove raw downstream affinity headers before signing the forward. The signed context MUST carry the recovered task's downstream turn-state and recovery-lane identity, and the target MUST NOT send retired aliases to the upstream account.

On process startup, a recent recovery row owned by the restarting instance MUST be retained as ownerless `DRAINING` continuity proof with an expired/released lease and unchanged `last_seen_at`. Recovery rows older than the existing ownerless cutoff MUST be deleted with their aliases, after which ordinary broad-alias resolution applies.

#### Scenario: Recovery reconnect stays on B

- **GIVEN** a verified replay established its recovery lane on account B
- **WHEN** the lane reconnects, prewarms, retries authorization, or changes to a compatible descendant session
- **THEN** account B is required through typed owner provenance
- **AND** no stale account-A affinity header reaches the new upstream connection

#### Scenario: Recovery lane is forwarded to its current replica owner

- **GIVEN** a later task turn reaches a non-owner replica with raw stale session aliases and a recovered downstream turn-state
- **WHEN** that replica forwards the recovery lane
- **THEN** the raw aliases are removed before signing
- **AND** only the recovered downstream turn-state and recovery identity are authenticated to the owner replica

#### Scenario: Replica restarts with recent recovery proof

- **GIVEN** a recent recovery row is owned by the restarting replica
- **WHEN** startup purges rows associated with that instance
- **THEN** the row becomes ownerless `DRAINING` proof without refreshing `last_seen_at`
- **AND** its specific alias continues to outrank a stale broad session alias

#### Scenario: Recovery proof ages past the cutoff

- **GIVEN** an ownerless or restarting-instance recovery row is older than the existing cutoff
- **WHEN** startup cleanup runs
- **THEN** the row and its aliases are deleted
- **AND** the stale proof no longer overrides ordinary broad-alias resolution

### Requirement: Ambiguous HTTP bridge prewarm dispatch retires the socket

If a prewarm `response.create` send may have started and its task is cancelled or otherwise interrupted before a terminal response, the service MUST mark the bridge closed and retiring before releasing response-create admission. Cleanup and retirement MUST finish despite repeated task cancellation. The service MUST NOT remove the prewarm demultiplexing state and then allow a visible request to reuse the same ambiguous socket.

#### Scenario: Prewarm task is cancelled during send

- **GIVEN** a prewarm frame may have been handed to the upstream transport
- **AND** a visible request is waiting on the same response-create gate
- **WHEN** the prewarm task is cancelled before a terminal response
- **THEN** the bridge is marked closed and retiring before the gate is released
- **AND** the admitted visible request is rejected without reconnecting or sending on that socket
- **AND** prewarm admission, pending state, account leases, and upstream resources are released exactly once

## MODIFIED Requirements

### Requirement: File-pinned compact refresh/connect failures fail closed

The proxy SHALL preserve file-owner routing during pre-visible refresh and upstream-connect failure handling. If the pinned account cannot refresh or open the upstream compact connection before any compact response is emitted, the proxy MUST surface a stable upstream-unavailable failure for that request instead of excluding the pinned account and replaying the compact request on another account. This fail-closed rule applies only to file-pinned compact requests; replayable compact/connect requests without a live file-id pin continue to use the existing pre-visible forced-refresh and eligible-account failover behavior.

#### Scenario: file-pinned compact request fails closed on refresh transport failure

- **GIVEN** `file_pinned` was uploaded through `account_a` and its in-memory pin is live
- **AND** a compact request references `{"type": "input_file", "file_id": "file_pinned"}`
- **WHEN** `account_a` fails token refresh with a pre-visible transport or connection error
- **THEN** the proxy returns an upstream-unavailable error for that compact request
- **AND** it does not select another account for that request

#### Scenario: replayable compact request without file pins can still fail over

- **GIVEN** at least two accounts are eligible for a compact request
- **AND** the compact request has no live `input_file.file_id` routing pin
- **WHEN** the selected account fails before compact output is emitted and the failure is classified by an existing pre-visible failover rule
- **THEN** the proxy may exclude that account for the current request and try another eligible account

#### Scenario: retained file-backed bridge replay remains owner-bound

- **GIVEN** an HTTP bridge precreated request uses a proxy-injected `previous_response_id` anchor
- **AND** the retained retry-safe full body references an account-scoped uploaded file through `input_file.file_id` or file-backed `input_image`
- **WHEN** the bridge retries after an upstream close before visible output
- **THEN** the proxy keeps the anchored request owner-bound instead of stripping the anchor, excluding the owner, and replaying the file reference on another account
- **AND** if the file owner cannot be reselected, the retry fails closed instead of reconnecting the bridge on a replacement account

#### Scenario: post-selection owner failure remains owner-bound

- **GIVEN** a streaming bridge request selects the required previous-response owner and holds an account stream lease
- **AND** the request contains an otherwise verified full resend
- **WHEN** refresh, authentication, WebSocket connection, transport, or timeout fails before output
- **THEN** the failed owner's stream lease is released
- **AND** the request keeps the ordinary post-selection failure classification
- **AND** the proxy does not exclude the selected owner or activate cross-account full-resend recovery
