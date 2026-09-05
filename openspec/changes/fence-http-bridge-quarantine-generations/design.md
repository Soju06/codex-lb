## Design

The quarantine registry remains an in-memory map owned by one proxy service.
Each entry keeps its key, reason, TTL/last-touch fields, and a weak reference
to the session that armed the entry. A service-level counter allocates the next
generation before the entry is updated. The counter survives entry pruning and
is never derived from the current map alone.

Primary-key cleanup is identity fenced. When `_http_bridge_sessions` has a
canonical value for the key, only that exact session may clear the entry; the
canonical registry wins over a detached object's weak owner token. The weak
owner is a fallback only when no canonical primary is registered, which also
protects an inactive first-strike entry from a detached predecessor. A
completion captures the primary-key generation (including an observed
absence) before any await that can arm a replacement. Only that exact captured
generation may be cleared, so a completion cannot remove a newer entry armed
while retry-circuit settlement is in flight. Mutable `session.quarantined`
flags and recycled object ids are never authority.

Recovery-origin cleanup is observation fenced. The recovery captures the active
generation before authorization. If it observed no entry, it passes `None` and
must not clear an entry that appeared later. If it observed a generation, only
the exact surviving generation may be cleared; a pruned-and-reused key or any
new arm is left intact. The same rule applies when the recovery-origin key is
also the completing session's primary key.

TTL and size pruning remain the only automatic expiry mechanisms. Admission of
a new key first prunes expired entries and then evicts the oldest non-poison
fence when the size cap needs a slot. Active non-poison and inactive
first-strike entries share one stable order: last-touch time, generation, then
the complete session-key tuple. If every slot contains an active poison
fence, the new arm is rejected: the service neither evicts poison evidence nor
grows the registry past its hard cap, and the rejected session remains
unquarantined. Because the rejected key has no entry to consult, the service
extends one bounded poison-overflow deadline to cover the rejected arm and all
currently active poison deadlines; anchor checks treat unknown keys as poison
until that deadline expires. This conservative service-level fallback may
temporarily fail closed for keys that have no retained entry, but it keeps the
anchor-proven-dead path safe without growing per-key memory. Re-arming an
existing key remains in-place and does not require another slot. Successful
completion clears only quarantine state; it does not touch retry-circuit,
account-health, routing, or durable-owner state.
Delta-only anchor selection is kept explicit in the main Responses contract: a
quarantined live session is absent as a local session candidate, but the
durable anchor remains available when the request itself does not carry a full
resend. In particular, a multi-item array containing only tool outputs is not
self-contained because its matching calls live behind that anchor.

## Proof seams

- Direct retirement and completion on one session clear a matching entry.
- A replacement under the same key keeps its newer entry when the detached
  predecessor completes.
- A generation captured before TTL pruning cannot clear a newly armed entry on
  the reused key.
- A recovery that observed absence cannot clear an entry armed while it was in
  flight, for both distinct-key and same-key cleanup.
- A primary completion that yields during retry-circuit settlement cannot clear
  a quarantine armed during that await.
- A primary completion that observes absence cannot clear a quarantine armed
  by a later durable load or settlement await.
- Revoke and poison-downgrade transitions allocate generations above every
  active generation on other keys.
- Weak references compare object lifetime rather than integer ids.
- Clearing matched poison provenance after a later first eventless strike
  removes only the poison arm and keeps the inactive strike counter for the
  next timeout.
- Poison cleanup captures both its provenance generation and the raw entry
  generation, so a first strike observed before capture is reset while only a
  strike that advances the raw generation afterward is retained.
- Durable-merge revocation carries the same poison provenance and raw
  generation pair, so revoking a speculative poison arm cannot erase a first
  strike recorded while persistence was in flight.
- Durable-load miss, purge, and reset revocation reuse the fence stored when
  the poison arm was installed, so a first strike recorded after that arm is
  not recaptured as pre-revocation evidence and discarded.
- The fence also captures the eventless-timeout count. A weaker quarantine arm
  may advance the raw generation without recording a strike, so cleanup keeps
  a first strike only when the count itself advanced after capture and drops
  expired suppressed-weaker evidence before making that decision.
- A new owner marks legacy-signature fallback requests as input-shape
  ambiguous. An exact canonical normalized raw-string one-item array is
  classified by its contained text length on that path, even though a genuine
  client array of the same form is byte-identical and cannot be distinguished.
  Only noncanonical one-item inputs keep the legacy compact-item predicate. A
  body-bound current-origin forward retains exact raw-string and raw-array
  classification.
- Current owner forwards advertise
  ``x-codex-bridge-input-shape-version: 2``. The value is included in the
  exact-body bridge signature and is trusted only when that signature
  validates; a missing, malformed, or primary-signature-only marker leaves
  the received payload in legacy compatibility mode.
- A current origin refuses owner dispatch before I/O when legacy normalization
  could reverse its delta-only classification: a below-boundary raw string
  whose normalized one-item array reaches the boundary, or a multi-item array
  containing only tool outputs. The origin accepts ring-level classifier proof
  only from a live advertisement whose process epoch equals the durable owner's
  recorded process epoch. An exact match permits forwarding to an upgraded
  owner; missing, malformed, stale, or epoch-mismatched proof leaves only the
  existing fail-closed or locally fenced recovery paths available. Binding the
  capability to the durable epoch prevents a replacement process from
  inheriting an earlier process's advertisement under the same instance id.
- A rejected quarantine admission is returned to its wrapper and caller; it
  does not mutate the rejected session's marker or pretend that the fence was
  installed. An already-active poison-overflow deadline is extended only when
  a retained poison arm extends its own deadline, so unknown keys remain
  fail-closed for the whole active poison window without creating a new
  overflow fence during ordinary admission.
