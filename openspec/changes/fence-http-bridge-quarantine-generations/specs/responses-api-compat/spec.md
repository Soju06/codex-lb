## MODIFIED Requirements

### Requirement: HTTP bridge quarantine cleanup is generation and identity fenced

The in-memory HTTP bridge quarantine registry MUST allocate generations from a
service-lifetime monotonic counter. Generation values MUST never be reused,
including after per-key removal, TTL/size-cap pruning, registry
reinitialization, or an allocator reset; any allocator reset MUST resume above
every generation already observed during that service lifetime. TTL and
size-cap pruning MUST NOT allow a later arm for a reused session key to receive
a generation already observed by an earlier completion. Each HTTP bridge
session lifetime MUST have an immutable, unique session-identity token
represented by that session object's object identity, distinct from reusable
bridge keys, account IDs, and session headers. Each entry MUST retain only a
weak reference to the session that armed it, so the bounded registry cannot
retain detached websocket sessions until TTL expiry; that weak reference is the
session-identity token used for fallback equality checks.

A primary-key completion MAY clear quarantine only when its completing session
is the current canonical session for a registered key, or when no canonical
primary is registered and the entry's weak owner is the completing session. If
the key is registered to a different session, the canonical registry wins and a
detached predecessor MUST NOT clear any entry or first-strike evidence for that
key, and an ownerless entry MUST remain uncleared when no canonical primary is
registered, regardless of a mutable per-session marker or recycled object id.
The completion MUST capture its immutable session-identity token together with
the primary-key quarantine generation, including an observed absence, before
taking its first await that can arm a replacement entry. Cleanup and equality
checks MUST use only those captured identity and generation/absence values. Only
the exact captured generation MAY be cleared; an observed absence or generation
mismatch MUST leave a raced entry active.

A stale-anchor recovery MUST capture the quarantine generation for its
recovery-origin key before authorization. A matching generation MAY be cleared
on successful completion. An observed absence (`None`) and a mismatched,
expired, pruned, or replaced generation MUST NOT clear an entry armed while the
recovery is in flight. This fence MUST apply when the origin key is distinct
from the completing session's key and when both keys are the same.

For a poison quarantine, the cleanup fence MUST capture the poison provenance
generation, the entry's raw generation, and its eventless-timeout count at the
same observation.
Clearing matched poison provenance MUST retain an inactive first-strike counter
only when both the raw generation and eventless-timeout count advanced after
that capture; a strike already present at capture MUST be reset with the poison
arm. An expired suppressed weaker fence MUST be discarded before cleanup
decides whether a post-capture first strike survives. The same captured-count
rule MUST apply when a durable retry-circuit merge revokes a speculative poison
arm.

Quarantine cleanup MUST remain independent from retry-circuit state, account
health, routing score, account eligibility, and durable bridge ownership. A
successful replay MAY clear quarantine without clearing or settling a retry
circuit. TTL and size-cap pruning MUST remain bounded and self-recovering. When
admitting a new key, the registry MUST prune expired entries and evict only the
oldest weaker-fence entries needed to stay within its hard size cap. An active
poison fence whose poison-specific deadline has not expired MUST never be
evictable. Inactive first-strike entries and active
non-poison quarantine entries MUST share one eviction tier ordered by ascending
last-touch time, then ascending generation, then the key tuple (affinity kind,
affinity key, API-key id with null ordered as the empty string, and strength).
If every
slot holds an active poison fence, the new arm MUST be rejected instead of
evicting poison evidence or growing the registry past the cap; the rejected
session MUST remain unquarantined and the rejection MUST NOT allocate or
mutate a quarantine generation. The service MUST maintain one bounded
poison-overflow deadline for rejected poison arms, extending it through the
required window of the rejected arm and every active retained poison fence;
while that deadline is active, anchor checks for an unknown rejected key MUST
fail closed as poison evidence. The same overflow verdict MUST govern a later
retained entry for that key whenever the entry does not itself carry active
poison evidence, including an inactive first-strike entry admitted after a slot
opens. Re-arming an existing key MAY refresh its entry in place without
consuming another slot. Quarantine admission wrappers MUST return whether the
requested fence was installed, and callers MUST consume a rejected result
without treating the session as quarantined.

#### Scenario: Detached predecessor cannot clear a replacement

- **GIVEN** a predecessor session quarantines a primary bridge key
- **AND** a replacement session becomes the canonical registry value for that
  key and receives a newer quarantine generation
- **WHEN** the detached predecessor completes and runs primary-key cleanup
- **THEN** the replacement's quarantine remains active
- **AND** the replacement generation remains authoritative

#### Scenario: Primary completion cannot clear a first strike recorded during settlement

- **GIVEN** a primary completion observes no active quarantine for its key
- **WHEN** retry-circuit settlement yields and another request records the first
  eventless strike for that key before completion cleanup resumes
- **THEN** the completion leaves that inactive first-strike evidence in the
  registry
- **AND** the next eventless timeout can still observe it as the prior strike

#### Scenario: TTL pruning and key reuse do not recycle a generation

- **GIVEN** a recovery observes a quarantine generation for a key
- **WHEN** that entry expires and is pruned, the key is quarantined again, and
  the recovery completes
- **THEN** the new quarantine generation differs from the observed generation
- **AND** the stale recovery cannot clear the new entry

#### Scenario: Observed absence cannot clear a raced quarantine

- **GIVEN** a recovery observes no quarantine for its origin key
- **WHEN** another session quarantines that key before recovery completion
- **THEN** the raced quarantine remains active
- **AND** this holds for both distinct-origin-key and same-key recovery

#### Scenario: Weak identity fences object lifetime

- **GIVEN** two distinct session objects reuse one primary key
- **WHEN** the predecessor completes after the replacement is canonical
- **THEN** cleanup compares weak object identity and leaves the replacement
  quarantine active even if an integer object id would collide

#### Scenario: The hard cap rejects a new key when poison fences fill it

- **GIVEN** the quarantine registry already holds its maximum number of
  active poison fences
- **WHEN** a different session key attempts to arm a quarantine
- **THEN** the new arm is rejected without evicting any poison fence
- **AND** the registry remains at its maximum size with the existing
  generations unchanged
- **AND** the rejected session is not marked quarantined
- **AND** an anchor check for the rejected key fails closed while the bounded
  poison-overflow deadline is active

#### Scenario: Overflow poison survives later inactive admission

- **GIVEN** a full registry rejected a poison arm for a key and recorded an
  active service-level overflow deadline
- **WHEN** a slot opens and the key records a first eventless timeout as an
  inactive entry
- **THEN** an anchor check for the key still fails closed under the overflow
  deadline
- **AND** the inactive entry does not report the poisoned anchor safe

### Requirement: Quarantine selection distinguishes local reuse from durable context

An active quarantine MUST make every live session under its key unavailable for
local session reuse and MUST make that live session count as absent when
determining whether a local bridge can supply an anchor. A full-conversation
resend MAY therefore suppress proxy anchor injection and proceed with its own
untrimmed input. A genuine delta-only continuation MUST retain access to its
durable anchor, because quarantine does not erase durable context and the
request has no equivalent replacement context source. This distinction MUST
not mutate account health, routing, or durable ownership.

For this requirement, the canonical full-resend-shape predicate MUST inspect
the decoded Responses request's `input` before durable lookup or replay
projection. It is true for a string with at least 4096 characters, an array
with more than one item unless every item is a `function_call_output`,
`custom_tool_call_output`, or `apply_patch_call_output`, or a one-item array whose compact serialization of the
entire array (`ensure_ascii=true` and no separator whitespace) is at least 4096
characters. Shorter strings and arrays, empty or null input, and any other
shape MUST remain delta-only. A multi-item array containing only those tool
output items is delta-only because their corresponding calls exist only behind
the continuation anchor. Exactly 4096 is included and 4095 is not. A
serialization failure MUST classify the one-item array as delta-only. This is
only a payload-shape signal and does not establish durable full-resend proof,
prefix identity, or account-neutral replay safety. Request validation MUST
preserve a client-supplied string's original shape and character length for
this decision; normalizing that string into a one-item array MUST NOT add the
array envelope to its boundary calculation. An internal HTTP bridge
owner-forward hop MUST preserve that original string shape so the owner's
request validation reaches the same classification as the origin.
During a rolling upgrade, when an older origin forwards only a normalized
one-item array and the owner cannot validate the additive exact-body signature,
the owner MUST use conservative canonical-shape precedence. An exact canonical
normalized raw-string shape (`role=user` with one `input_text` part) MUST be
classified by its contained text length, not by its array or item serialization.
That wire shape is byte-identical to a genuine client array of the same form, so
the owner cannot recover the original provenance; the contained-text rule
therefore applies to both origins. A noncanonical one-item array MUST retain the
legacy compact-item predicate. Neither path may count a normalization envelope
as client text. In the
inverse rolling-upgrade direction, an upgraded origin without positive proof
that the selected owner implements this classifier MUST NOT dispatch a
delta-only shape that the legacy owner would classify as a full resend. This
guard MUST cover a client string below 4096 characters whose normalized
one-item array reaches 4096 compact-serialization characters, and a multi-item
array containing only the allowed tool-output item types. The origin MUST fail
closed or enter an already-authorized local recovery path before owner I/O.
Positive proof MUST come from a live bridge-ring advertisement containing the
exact input-shape-classifier capability and a process epoch equal to the
durable owner's recorded `owner_process_epoch`. When that proof matches, the
origin MAY dispatch the ambiguous delta-only shape to the upgraded owner.
Missing, malformed, stale, or epoch-mismatched advertisements MUST NOT
authorize dispatch, including an advertisement left by an earlier process
that reused the same instance id.

An upgraded owner-forward request MUST advertise
`x-codex-bridge-input-shape-version: 2` when it posts a body whose exact input
shape is known. The value MUST be included in the exact-body bridge signature.
The owner MUST trust the current-shape mode only when that signature validates
with the same header value; a missing, malformed, or primary-signature-only
marker MUST keep legacy compatibility classification. An unsupported nonempty
version MUST be rejected before the forwarded request reaches continuity
selection.

#### Scenario: Quarantine preserves durable context for delta-only requests

- **GIVEN** a live bridge session is quarantined and its durable anchor is
  available
- **WHEN** a genuine delta-only continuation arrives for that session key
- **THEN** the quarantined live session is excluded from local reuse and
  full-resend anchor injection
- **AND** the request still resolves and receives its durable anchor
- **AND** no account health, routing, or durable ownership state changes

#### Scenario: Legacy owner forwarding uses canonical normalized text length

- **GIVEN** an older origin normalized a below-boundary client string into a
  one-item array before forwarding it to a newer owner
- **AND** the forward validates only through the rolling-upgrade legacy
  signature fallback
- **WHEN** the newer owner classifies the request shape
- **THEN** it MUST classify the exact canonical normalized shape by its
  contained text length, not by its normalization envelope
- **AND** it MUST retain the durable previous-response anchor

#### Scenario: Legacy fallback uses the compact-item predicate for noncanonical arrays

- **GIVEN** an older origin forwards a genuinely noncanonical one-item array
  such as `["x" * 4094]`
- **AND** the forward validates only through the rolling-upgrade legacy
  signature fallback
- **WHEN** the newer owner classifies the request shape
- **THEN** it MUST use the compact serialization of that item for the 4096-byte
  boundary
- **AND** it MUST classify the item as full-resend-shaped at exactly 4096
  characters

#### Scenario: Unauthenticated input-shape marker stays legacy

- **GIVEN** a forwarded body carries `x-codex-bridge-input-shape-version: 2`
- **AND** its exact-body signature is missing or does not bind that marker
- **WHEN** the owner validates the primary bridge signature
- **THEN** it MUST accept only under legacy compatibility classification
- **AND** it MUST NOT infer current-shape mode from the marker alone

#### Scenario: Current origin does not expose a delta to a legacy owner

- **GIVEN** an upgraded origin selects a remote owner whose current classifier
  capability is not positively known
- **AND** the request is delta-only under the current classifier but full-resend
  shaped after legacy normalization
- **WHEN** the origin reaches the owner-forward boundary
- **THEN** it MUST NOT dispatch the request to that owner
- **AND** it MUST fail closed or use an already-authorized local recovery path
  before the legacy owner can suppress the durable anchor

#### Scenario: Proven upgraded owner receives an ambiguous delta

- **GIVEN** an upgraded origin selects a live remote owner
- **AND** the ring advertises the exact input-shape-classifier capability with
  a process epoch equal to the durable owner's recorded process epoch
- **AND** the request is delta-only under the current classifier but
  full-resend shaped after legacy normalization
- **WHEN** the origin reaches the owner-forward boundary
- **THEN** it MAY dispatch the request to that owner
- **AND** the owner MUST retain the durable previous-response anchor

#### Scenario: Replaced owner process cannot inherit capability proof

- **GIVEN** an instance id has a classifier-capable ring advertisement from an
  earlier owner process
- **AND** the durable owner record names a different current process epoch
- **WHEN** an upgraded origin evaluates an ambiguous delta-only owner forward
- **THEN** the stale advertisement MUST NOT authorize dispatch
- **AND** the origin MUST fail closed or use an already-authorized local
  recovery path before owner I/O
