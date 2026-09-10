## ADDED Requirements

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
projection. It MUST classify each shape as follows:

- A string is full-resend-shaped if it contains at least 4096 Unicode code
  points; a shorter string is delta-only.
- An array with more than one item is full-resend-shaped unless every item is
  a `function_call_output`, `custom_tool_call_output`, or
  `apply_patch_call_output`. An array containing only those output items is
  delta-only because their corresponding calls exist behind the anchor.
- A one-item array containing one of those tool-output types is delta-only,
  regardless of output length. Other one-item arrays are full-resend-shaped if
  the compact serialization of the
  entire array (`ensure_ascii=true` and no separator whitespace) contains at
  least 4096 characters. A shorter serialization or a serialization failure
  is delta-only.
- Empty arrays, null input, and all other shapes are delta-only.

Every length threshold in the current and legacy predicates MUST count
characters, not UTF-8 bytes. Raw text uses Unicode code points; compact JSON
uses the characters in its ASCII-escaped serialization, including escapes and
punctuation. Exactly 4096 is included and 4095 is not. This is only a
payload-shape signal and does not establish durable full-resend proof,
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
that the selected owner implements this classifier MUST NOT dispatch an input
whose current and legacy classifications disagree in either direction. This
guard MUST cover a client string below 4096 characters whose normalized item
reaches 4096 compact-serialization characters, a multi-item array containing
only the allowed tool-output item types, a single tool output whose legacy
item serialization reaches 4096 characters, and a one-item array whose whole-array
serialization reaches 4096 characters while its item serialization does not.
It MUST also cover full-resend-shaped system/developer-only arrays that
normalize to empty input. Truly empty input and small single-message arrays
whose current and legacy classifications are both delta-only MUST remain
outside this upgrade requirement.
The origin MUST fail
closed or enter an already-authorized local recovery path before owner I/O.
For a request with turn state and no client `previous_response_id`, the typed
`owner_forward` / `owner_input_shape_upgrade_required` failure MUST be eligible
for the existing local turn-state takeover path. Takeover MUST still require a
successful fresh durable lookup with no active owner lease and the existing
continuity-routing checks. A failed lookup or active lease MUST fail closed;
missing owner capability proof MUST NOT permit owner dispatch.
A proxy-injected durable anchor MUST NOT count as a client-supplied previous
response id for this decision. The origin MUST resolve fresh turn-state
ownership without using that injected anchor as a lookup alias, and any retained
anchor MUST remain constrained to its original account.
Positive proof MUST come from a live bridge-ring advertisement containing the
exact input-shape-classifier capability and a process epoch equal to the
durable owner's recorded `owner_process_epoch`. When that proof matches, the
origin MAY dispatch the classification-ambiguous shape to the upgraded owner.
Missing, malformed, stale, or epoch-mismatched advertisements MUST NOT
authorize dispatch, including an advertisement left by an earlier process
that reused the same instance id.

Capability-gated forwards MUST carry `x-codex-bridge-owner-process-epoch`
with the proven process epoch, authenticated by the exact-body signature.
The receiving owner MUST reject a signed epoch unequal to its local process
epoch before continuity selection. A nonempty process epoch MUST NOT be
authorized by either the legacy primary proof or the public pre-input-shape V2
proof, because neither codec authenticates that field. These forwards MUST carry only
`x-codex-bridge-input-shape-signature-v2` as their body proof and MUST NOT
include either the legacy primary signature or the public
`x-codex-bridge-signature-v2` proof. A predecessor process that ignores the
epoch therefore cannot accept the request after a rollback.

An upgraded owner-forward request MUST advertise
`x-codex-bridge-input-shape-version: 2` when it posts a body whose exact input
shape is known. The exact posted body, marker value, and optional owner process
epoch MUST be authenticated by the independent
`x-codex-bridge-input-shape-signature-v2` proof.
The owner MUST trust the current-shape mode only when that signature validates
with the same header value; a missing, malformed, or primary-signature-only
marker MUST keep legacy compatibility classification. Receivers implementing
this versioned input-shape contract MUST reject an unsupported nonempty
version before the forwarded request reaches continuity selection.

For ordinary forwards, `x-codex-bridge-signature-v2` MUST retain the public
pre-input-shape codec: it signs `model_dump_for_forwarding()` and MUST NOT add
the input-shape marker or owner process epoch to its structured payload. The
origin MUST send this proof alongside the independent exact-shape proof so a
pre-input-shape owner can authenticate file-bound forwards without using the
legacy primary fallback. An upgraded receiver MUST also accept the previously
deployed shape-v2 layout in which the exact-shape proof bytes were carried in
`x-codex-bridge-signature-v2`; this acceptance MUST still require the signed
marker and, when present, the matching local owner process epoch.

The marker advertises the origin's input-shape provenance, not the selected
owner's capability. For inputs whose current and legacy classifications agree,
forwarding MUST NOT require classifier capability proof and MUST retain the
existing primary-signature fallback, subject to its existing restrictions.
A predecessor owner may ignore the additive marker and exact-shape proof and
validate the public full-context proof, including for file-bound requests.
The origin MUST NOT remove a known-shape marker merely because the destination
lacks classifier capability proof.

A payload already classified under legacy forwarding MUST omit the marker
when forwarded again. Its exact-body signature MUST bind the posted body
without an input-shape version. A current receiver accepting that signature
MUST retain legacy compatibility classification. This MUST NOT bypass the
capability and process-epoch checks for classification-ambiguous requests.

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
- **THEN** it MUST use the compact serialization of that item for the 4096-character
  boundary
- **AND** it MUST classify the item as full-resend-shaped at exactly 4096
  characters

#### Scenario: Unauthenticated input-shape marker stays legacy

- **GIVEN** a forwarded body carries `x-codex-bridge-input-shape-version: 2`
- **AND** its exact-body signature is missing or does not bind that marker
- **WHEN** the owner validates the primary bridge signature
- **THEN** it MUST accept only under legacy compatibility classification
- **AND** it MUST NOT infer current-shape mode from the marker alone

#### Scenario: Owner is replaced after capability proof

- **GIVEN** an origin proved the selected owner's process epoch and classifier
  capability for an ambiguous delta-only request
- **WHEN** a replacement process receives the forward at the same instance id
- **THEN** an upgraded replacement MUST reject the signed process-epoch mismatch
- **AND** a predecessor replacement MUST fail signature validation without a
  legacy primary fallback
- **AND** neither replacement may select continuity or suppress the durable anchor

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

#### Scenario: Large single tool output retains its continuation anchor

- **GIVEN** a quarantined session has a durable turn-state anchor
- **AND** a request contains one tool output whose serialized size exceeds 4096 characters
- **AND** the client omits `previous_response_id`
- **WHEN** the bridge classifies the request
- **THEN** it MUST treat the input as delta-only and retain the durable anchor
- **AND** forwarding to an owner with a disagreeing legacy classifier MUST require the existing capability and process-epoch proof
