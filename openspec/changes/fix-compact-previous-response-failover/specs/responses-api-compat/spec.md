## ADDED Requirements

### Requirement: Compact requests recover from selection-time previous-response owner loss

When a compact request is pinned to a previous-response owner account, that pin is the only
continuity pin (no client-supplied turn-state owner and no input-file owner), and account
selection cannot return the pinned owner, the proxy MUST attempt account-neutral fresh-replay
recovery before surfacing the failure, provided the compact payload is a locally verified
account-neutral full resend. Local verification MUST run against the exact upstream-bound
compact payload without `previous_response_id`, after every wire transformation the compact
serializer applies. It MUST require that serialized `input` to be a list of more than one item
that is item-for-item identical to the validated request `input`, so that no request whose wire
history is dropped or trimmed — including single-item collapse and oversized-input trim markers
— is ever replayed on another account, which could not resolve the omitted owner-resident
context. It MUST validate that same serialized payload against the
shared account-neutral fresh-replay rules: self-contained tool call/output pairing, no server-assigned item ids, no encrypted or compaction state, no
nonblank conversation or prompt handles, no account-scoped file/container/vector handles, no
hosted/MCP call state, and only recognized account-neutral fields and shapes.

A self-contained wire payload alone MUST NOT authorize dropping the anchor, because a client
may send only the turns that follow `previous_response_id` and rely on the owner to hold the
earlier conversation. Recovery MUST additionally require independently trusted proxy-side
evidence that the request still carries the anchored conversation: the durable continuity
record for that `previous_response_id` MUST exist, MUST NOT name an account other than the
pinned owner, MUST carry a recorded input prefix count and prefix fingerprint, and the
request `input` MUST strictly extend that recorded prefix with an item-for-item fingerprint
match. A missing record, a missing recorded prefix, a durable owner mismatch, a prefix
fingerprint mismatch, or a failed durable lookup MUST keep the request owner-bound.

For an eligible recovery, the proxy MUST remove `previous_response_id` from the upstream
compact payload, strip downstream session/turn affinity aliases from the upstream-bound
headers, clear sticky affinity for the retried selection, exclude the unavailable owner
account from the remaining attempts, and reselect among the remaining eligible accounts.

When the payload is not a verified account-neutral full resend, the proxy MUST keep the
existing fail-closed failure for that request, MUST NOT send any of the payload to another
account, and MUST record the fail-closed outcome on the continuity fail-closed observability
counter for the compact surface. Recovery MUST NOT activate when the pin includes a
client-supplied turn-state owner or an input-file owner, or when the previous-response owner
cannot be resolved at all. Post-selection refresh, authentication, transport, and timeout
failures on the pinned owner keep their existing owner-bound handling: recovery MUST only
activate when the pinned owner was never used for the request at selection time, or when the
pinned owner was excluded mid-request by a pre-visible quota or rate-limit failure that permits
failover.

#### Scenario: Quota-exhausted previous-response owner fails over with a verified full resend

- **GIVEN** account A owns the previous response referenced by a compact request and account B is eligible
- **AND** account A is rate-limited or quota-exhausted with an unelapsed reset
- **AND** the compact payload carries an account-neutral full-resend `input`
- **AND** the durable continuity record for the anchor names account A and records a prefix the request `input` still opens with
- **WHEN** pinned account selection cannot return account A
- **THEN** the proxy sends the compact upstream exactly once on account B without `previous_response_id`
- **AND** downstream session/turn affinity aliases are not sent to account B
- **AND** the compact response is returned successfully

#### Scenario: Owner exhausts quota during the compact request

- **GIVEN** the pinned previous-response owner is selected for a compact request
- **AND** the upstream compact fails with a pre-visible quota or rate-limit error that permits failover
- **WHEN** reselection cannot return the now-excluded owner
- **THEN** the proxy applies the same account-neutral fresh-replay recovery on another eligible account
- **AND** the owner's quota failure is not surfaced to the client when the recovery succeeds

#### Scenario: Non-neutral compact payload stays fail-closed

- **GIVEN** a pinned compact request whose `input` retains encrypted compaction state, server-assigned item ids, or account-scoped file handles
- **WHEN** the pinned owner cannot be selected
- **THEN** the request fails with the existing selection or upstream error
- **AND** no part of the payload is sent to another account
- **AND** the continuity fail-closed counter records the compact-surface outcome

#### Scenario: History the wire serializer shortens stays fail-closed

- **GIVEN** a pinned compact request whose `input` loses history when serialized for upstream, either collapsing to a single item or being trimmed to a head, trim marker, and tail
- **WHEN** the pinned owner cannot be selected
- **THEN** the request fails with the existing selection or upstream error
- **AND** the proxy does not replay the shortened history on another account

#### Scenario: Unproven anchored history stays fail-closed

- **GIVEN** a pinned compact request whose serialized `input` is an account-neutral multi-item history
- **AND** the durable continuity record for `previous_response_id` is missing, records no input prefix, names a different account, or records a prefix the request `input` does not open with
- **WHEN** the pinned owner cannot be selected
- **THEN** the request fails with the existing selection or upstream error
- **AND** the proxy keeps the anchor and sends no part of the payload to another account

#### Scenario: Post-selection authentication failure on the pinned owner stays owner-bound

- **GIVEN** the pinned previous-response owner is selected for a compact request with an account-neutral full-resend `input`
- **AND** the upstream compact fails with `401` again after the forced token refresh, which excludes the owner from the remaining attempts
- **WHEN** reselection cannot return the now-excluded owner
- **THEN** the proxy surfaces the owner's authentication failure
- **AND** account-neutral fresh-replay recovery does not activate and no part of the payload is sent to another account

#### Scenario: Turn-state-pinned compact remains owner-bound

- **GIVEN** a compact request pinned by a real client-supplied turn-state owner
- **WHEN** that owner account cannot be selected
- **THEN** the request fails closed with the existing retryable continuity or saturation error
- **AND** account-neutral fresh-replay recovery does not activate

#### Scenario: Unresolvable previous-response owner remains fail-closed

- **GIVEN** a compact request whose `previous_response_id` owner cannot be resolved from any record
- **AND** more than one account is eligible
- **WHEN** the request is evaluated before account selection
- **THEN** the request fails with `previous_response_owner_unavailable`
- **AND** the proxy does not treat the missing owner as a selector result or replay on another account
