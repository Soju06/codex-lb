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
pinned owner, MUST report the requested `previous_response_id` as its own latest response —
because the recorded prefix is session-level and every later response registration overwrites
it, so a record that moved on describes a different turn — MUST carry a recorded input prefix
count and prefix fingerprint, and the
request `input` MUST strictly extend that recorded prefix with an item-for-item fingerprint
match. Because a matching prefix still permits a resend that omits the anchored response's own
output, the items after the recorded prefix MUST also retain that response's completed
assistant output, with any tool calls settled, before any new client input, using the same
retained-output rule the HTTP bridge replay path applies. A missing record, a record whose latest response is not the
requested anchor, a missing recorded prefix, a durable owner mismatch, a prefix fingerprint
mismatch, a suffix that does not retain the anchored output ahead of new input, or a failed
durable lookup MUST keep the request owner-bound.

For an eligible recovery, the proxy MUST remove `previous_response_id` from the upstream
compact payload, strip downstream session/turn affinity aliases from the upstream-bound
headers, clear sticky affinity for the retried selection, exclude the unavailable owner
account from the remaining attempts, and reselect among the remaining eligible accounts.

A recovered compaction returns account-scoped compaction state from the replacement account,
so after the compact succeeds the proxy MUST move the conversation's continuity ownership to
the account that served it. It MUST rebind the client's sticky session mapping and MUST rebind
the durable continuity session that proved the anchored history, so that session names the
replacement account and no longer publishes the lost owner's anchor or turn state. It MUST NOT
rebind a hard turn-state sticky key, because that key is the lost owner's own opaque state and
pointing it at another account would authorize the cross-account send the rebind prevents.
Because the compaction already succeeded upstream and a retry would repeat the same recovery,
a rebind failure MUST be logged and MUST NOT fail the returned compaction.

The durable rebind MUST be conditional on the proving durable session still being the session
that was observed: an ordinary turn for the same canonical session can complete between the
proof and the post-success rebind, and the account change clears that session's aliases and
recorded anchor, so an unconditional move would erase continuity the newer turn has already
returned to its client. The proxy MUST therefore apply the durable move as a compare-and-set on
the observed session's owner epoch, account, and latest response, MUST leave the row to its
current owner when that comparison does not hold, MUST log that the rebind did not apply, and
MUST still return the successful compaction.

A turn that has already been submitted upstream but has not returned yet does not change
any of those completion-time fields, so the compare-and-set MUST additionally require the
durable session to be unowned: while a bridge worker holds the session, its own continuity
registration is fenced on that ownership, so moving the row would leave the response it has
already streamed to its client with no durable continuity. The proxy MUST leave a held session
to its owner and log that the rebind did not apply.

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
- **AND** the durable continuity record for the anchor names account A, reports the anchor as its latest response, and records a prefix the request `input` still opens with, followed by the anchored response's retained output and the new client input
- **WHEN** pinned account selection cannot return account A
- **THEN** the proxy sends the compact upstream exactly once on account B without `previous_response_id`
- **AND** downstream session/turn affinity aliases are not sent to account B
- **AND** the compact response is returned successfully

#### Scenario: Recovered compaction moves continuity ownership to the serving account

- **GIVEN** a compact request recovers on account B after the pinned owner account A is unavailable
- **AND** the request carries a session sticky key and the anchored history was proven by a durable continuity session naming account A
- **WHEN** the compaction succeeds on account B
- **THEN** the client's sticky session mapping resolves to account B
- **AND** the durable continuity session names account B and no longer publishes account A's anchor alias or recorded turn state
- **AND** a hard turn-state sticky key is left bound to account A

#### Scenario: A concurrent newer turn keeps its durable continuity

- **GIVEN** a compact request recovered on account B after the pinned owner account A was unavailable
- **AND** an ordinary turn for the same canonical session completed after the anchored-history proof and before the post-success rebind
- **WHEN** the proxy rebinds the durable continuity session
- **THEN** the compare-and-set does not apply and the durable session keeps the newer turn's account, anchor, and aliases
- **AND** the proxy logs that the durable rebind did not apply
- **AND** the successful compaction is still returned to the client

#### Scenario: A durable session held by a bridge worker keeps its owner

- **GIVEN** a compact request recovered on account B after the pinned owner account A was unavailable
- **AND** the proving durable continuity session is still held by a bridge worker with a turn in flight
- **WHEN** the proxy rebinds the durable continuity session
- **THEN** the durable session keeps account A, its recorded anchor, and its aliases
- **AND** the proxy logs that the durable rebind did not apply
- **AND** the client's sticky session mapping still resolves to account B

#### Scenario: Continuity rebind failure keeps the successful compaction

- **GIVEN** a compact request recovered on another account and the compaction succeeded upstream
- **WHEN** rebinding the sticky mapping or the durable continuity session fails
- **THEN** the proxy logs the rebind failure
- **AND** still returns the successful compaction to the client

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
- **AND** the durable continuity record for `previous_response_id` is missing, has moved on to a later response, records no input prefix, names a different account, records a prefix the request `input` does not open with, or records a prefix the request `input` follows without retaining the anchored response's output
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
