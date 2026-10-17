# Design notes: recover-poisoned-bridge-anchor-at-half-open

Non-normative context for the requirements in this change. The spec deltas in
`specs/` carry the contract; this file records why the contract took the shape
it did and which residual tradeoffs were accepted.

## Why the abandonment threshold is capped

The durable anchor abandonment compares against the capped poison threshold
instead of the raw configured setting because the retirement funnels used to
compare the raw value: with the default of seven and a circuit threshold of
two, the poisoned anchor stayed stored while the circuit was already cooling
on it, which is the unreachability this change exists to remove.

## Strict base-match CAS on strike writes

The retry-circuit upsert accepts a write only when its base equals the row's
current `updated_at_epoch`. Wall-clock recency cannot stand in for lineage: a
delayed write always carries a newer timestamp than the base it loaded, so
admitting "newer than base" writes would merge a finished episode's count
into whatever lineage owns the row now — including a reset row another
replica has already re-struck, where the zero-count reset check can no longer
fire. Dropping the write whole also keeps the row byte-identical, so
in-flight version fences (settles, generation claims, other writers' bases)
stay valid, and a stale write can no longer overwrite `last_detail` and
corrupt the poison-class check the clear consult depends on.

The accepted trade is undercounting: a concurrent same-lineage strike that
loses the race is dropped and only its own strike is lost, so the circuit
opens at most one failure later. The rejected alternative — merging — can
overcount across lineages, and overcounting is strictly worse: it opens
false cooldowns and can authorize abandoning a fresh anchor. Distinguishing
same-lineage concurrency from cross-reset staleness precisely would require
a durable lineage identity column, which is out of scope for this change.

## Why the persist merge adopts the returned row wholesale

The upsert returns the post-write row: when the write landed the row
reflects it, and when its base mismatched the row is the lineage that owns
the key now. Strikes for one key are serialized across their durable awaits,
so no local failure can be recorded while a persist is in flight, and the
returned row is simply adopted — count, cooldown, detail, and version.
Adoption compares no wall clocks: an earlier condition required the reset to
look "newer" than the local base, which permanently wedged a worker whenever
the resetting replica's clock lagged — the worker kept a false count and
cooldown, and the strict base-match upsert rejected every later strike
because its base epoch no longer existed on the row. Taking the row's epoch
exactly, never the max of two clocks, is what re-arms the next strike with a
base that exists.

## Why an outraced settle retries once

A fenced settle that matches no row was outraced by a concurrent writer, but
the settlement holds the newer evidence — the completed response proved the
key works — so it reloads the moved row and retries the fence against the
current version, mirroring the in-process rule where a completion's clear
wins over a strike written moments earlier.

## Why the continuity clear fences both columns

Turn-state aliases are written independently of response anchors, so a
continuity clear fenced on the response id alone would still match while
deleting a freshly registered turn state the episode never proved dead.
Capturing and fencing both columns makes any concurrent continuity write —
response anchor or turn state — fail the clear instead.

## Accepted residual: process-local one-clear marker

A completed verified stale-anchor replay deliberately keeps the source key's
circuit and its durable row (its generation fence suppresses further stale
replays), so the one-clear marker on that surviving episode is process-local
by design. A process that rehydrates the row without the marker abandons the
anchor one strike earlier than a fresh episode would, against a key whose
requests are already failing eventlessly — an accepted, bounded trade until
an episode marker is persisted with the row.

The residual has narrowed since it was recorded: the anchor-advance
suppression — the one marker path whose surviving row coexists with fresh
durable continuity — is now persisted by rewriting the row's failure detail
to the non-poison ``anchor_superseded`` class, so other replicas neither
arm quarantine against the fresh anchor nor authorize its abandonment. The
remaining process-local markers cover confirmed abandonments, which are
cross-replica safe already: their continuity columns are empty, and the
empty-continuity consult refuses another clear.

## Accepted residual: probe admission is process-local

The exactly-one-probe guarantee holds per worker process. In a multi-replica
deployment each replica that loads the expired durable cooldown can arm its
own local half-open lease and admit its own probe, so up to one probe per
replica can reach upstream concurrently; the durable generation claim
coordinates only verified stale-anchor replays. Coordinating ordinary probe
admission across replicas would require a durable lease (a schema change and
a durable write on the hot admission path), out of scope for this change.
The exposure is bounded: probes are one request per replica per cooldown
window, and a poisoned anchor is already quarantined out of probe planning.

## Accepted residual: remote half-open lease deadline is not durable

The half-open lease deadline lives in the process that admitted the probe.
When a dispatch claim is lost to a probe this process cannot see, the
suppression message reports the configured half-open lease duration as the
retry-after upper bound rather than the remote probe's actual remaining
lease; persisting the lease deadline would require a schema change and a
durable write on the admission path, out of scope for this change.

## Planning-time circuit staleness is bounded by the minimum cooldown

The pre-planning durable load runs on a worker's first touch of a hard key
(and again when continuity resolution replaces it with a different
canonical key), and its cached view is honored only while it is younger
than the minimum cooldown (60s). This bound is exactly sufficient rather
than per-request: a circuit another replica opens after this worker's last
load cannot have an expired cooldown until at least the minimum cooldown
has passed, by which time any planning pass has refreshed — so the
poisoned-anchor probe race is closed while keys with circuit history pay
at most one durable read per minimum-cooldown window instead of one per
request. (Keys with no circuit row never enter the cache and already
refresh per planning pass.)
