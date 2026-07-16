# model-catalog-compat delta: replica-aware-model-registry

## ADDED Requirements

### Requirement: Refreshed model catalog is replica-coherent

The leader refresh cycle SHALL persist the complete registry state (models, plan maps, per-account tier maps, suppression set, authoritative flags, metadata retention state, and the refresh wall-clock timestamp) to the single-row `model_registry_snapshot` table and SHALL bump the `model_registry` cache-invalidation namespace only after the persist commits (write-then-bump). The payload write and the bump SHALL be skipped when the serialized content hash is unchanged from the last persisted state AND the stored row was still within `model_registry_snapshot_max_age_seconds`; the stored `refreshed_at` timestamp SHALL still be advanced so snapshot age reflects the leader's latest successful refresh. When the content hash is unchanged but the stored row had already aged past `model_registry_snapshot_max_age_seconds` before this refresh revived it, the leader SHALL still bump the `model_registry` namespace (only the payload rewrite stays skipped): an expired row causes followers to clear their local registry and reset their applied-content-hash marker, so an unchanged-content revival still requires a bump for them to re-apply within the cache-invalidation poll bound instead of waiting for the non-leader scheduler backstop. Every replica MUST apply a newly persisted snapshot within the cache-invalidation poll bound and MUST invalidate its local account-selection cache on apply; that account-selection invalidation MUST be local-only (non-propagating), because reconcile only applies a change the leader already published (which bumped `model_registry` to reach every replica) and each replica clears its own selection cache on apply, so a propagating clear would make every follower durably re-bump `account_selection` and amplify bus traffic with no peer-visible effect. When the reconcile is driven by the `model_registry` invalidation callback and the snapshot load fails (transient DB read error or malformed payload), the callback MUST surface the failure to the invalidation poller so the poller leaves the `model_registry` version unacknowledged and retries on the next poll cycle (matching the `account_routing` refresh callback), rather than acknowledging the bump and stranding the replica on the stale catalog until the non-leader scheduler backstop; the startup one-shot reconcile and the refresh-tick backstop instead swallow such a load failure (keeping the current in-memory state) so they never fail startup or the scheduler loop. Payload decode MUST treat a set-backed or mapping-backed catalog field whose persisted value has the wrong type — for example a `model_plans`/`plan_models`/`model_accounts`/per-account tier entry persisted as a scalar or object where a list of slugs is expected, or a model entry that is not an object — as a malformed payload and raise, rather than silently dropping the offending entry and applying a partial catalog; a genuinely-absent or empty container (an absent key, an empty map, or an empty list) is not malformed and MUST decode successfully. After apply, `/v1/models`, plan gating (`plan_types_for_model`), suppression (`is_suppressed_model`), and per-account service-tier routing on a non-leader MUST be identical to the leader. A non-leader refresh tick MUST NOT fetch the upstream catalog and SHALL instead reconcile from the persisted snapshot when the stored snapshot header differs from the last applied one (backstop for a lost invalidation bump). A leader catalog clear SHALL persist an explicit cleared marker and bump, so followers revert to the bootstrap floor rather than serving a withdrawn catalog. Every replica SHALL install its `model_registry` cache-invalidation callback (the global invalidation poller) before starting the model refresh scheduler, so a first leader tick that persists a changed snapshot cannot silently drop its bump. Every replica SHALL record the invalidation-poller version baseline before running its one-shot startup reconcile, so a leader bump that lands in the window between that reconcile's snapshot read and the poller's first background tick is delivered as an invalidation callback (within the poll bound) rather than absorbed as the poller's initial callback-less baseline (which would defer convergence to the non-leader scheduler backstop). The baseline-priming read SHALL surface a failure to its caller (the poller MUST remain uninitialized) rather than silently continuing, so a transient failure of the startup seed is logged and explicitly degraded to first-poll-baseline behavior instead of being mistaken for a recorded baseline — otherwise the first successful background poll would absorb a peer bump as its initial callback-less baseline and void the delivery guarantee priming exists to provide.

#### Scenario: Follower serves the refreshed catalog on /v1/models

- **GIVEN** replica A (leader) completes a registry refresh whose catalog adds a new slug and withdraws a bootstrap slug
- **AND** replica A persists the snapshot and bumps the `model_registry` namespace
- **WHEN** replica B's cache-invalidation poller observes the version change
- **THEN** replica B applies the snapshot to its in-memory registry
- **AND** `GET /v1/models` served by replica B lists the new slug and omits the withdrawn slug

#### Scenario: Follower enforces suppression of a withdrawn slug

- **GIVEN** the leader's refreshed snapshot marks a previously served slug as suppressed
- **WHEN** a follower applies the persisted snapshot
- **THEN** `is_suppressed_model` returns true for that slug on the follower

#### Scenario: Follower enforces plan gating for a newly gated slug

- **GIVEN** the leader's refreshed snapshot maps a slug to exactly one plan type
- **WHEN** a follower applies the persisted snapshot
- **THEN** `plan_types_for_model` on the follower returns exactly that plan set instead of no filtering

#### Scenario: Catalog clear propagates to followers

- **GIVEN** the leader clears the registry because no active accounts remain
- **WHEN** the leader persists the cleared marker and bumps, and a follower applies it
- **THEN** the follower reverts to the bootstrap catalog floor

#### Scenario: Lost bump converges via the refresh-tick backstop

- **GIVEN** a snapshot was persisted but the invalidation bump was lost
- **WHEN** a non-leader replica's next refresh tick runs
- **THEN** the replica detects the header mismatch, applies the persisted snapshot, and converges within one refresh interval

#### Scenario: Transient load failure in the callback is retried, not acknowledged

- **GIVEN** the leader persisted a changed snapshot and bumped the `model_registry` namespace
- **AND** a follower's snapshot load transiently fails on the invalidation callback (e.g. a DB read error or a momentarily unreadable payload)
- **WHEN** the follower's poll cycle runs the callback and it fails
- **THEN** the poller does not acknowledge the observed `model_registry` version and retries the callback on the next poll cycle
- **AND** once the transient failure clears, the retry applies the persisted snapshot within the poll bound without requiring a new leader bump

#### Scenario: Malformed set-backed field is rejected, not silently dropped

- **GIVEN** the leader bumped the `model_registry` namespace and the persisted payload is valid JSON but a set-backed field is wrong-typed (e.g. `model_plans` maps a slug to `{"gpt-x": "pro"}` instead of a list of plan slugs)
- **WHEN** a follower's invalidation callback loads and decodes the payload
- **THEN** the decode raises rather than dropping the offending entry
- **AND** the poller leaves the `model_registry` version unacknowledged and no partial catalog is applied (the follower keeps its prior in-memory state and retries on the next poll)

#### Scenario: Empty set-backed maps decode successfully

- **GIVEN** a persisted snapshot whose set-backed fields are genuinely empty (empty maps, or a slug mapped to an empty list)
- **WHEN** a replica decodes the payload
- **THEN** the decode succeeds and the corresponding sets are empty (empty is not treated as malformed)

#### Scenario: Applying a snapshot does not re-bump account_selection

- **GIVEN** the leader persisted a changed snapshot and bumped `model_registry`
- **WHEN** a follower applies the snapshot and invalidates its local account-selection cache
- **THEN** the follower does not enqueue or write an `account_selection` cache-invalidation bump

#### Scenario: Non-leader tick performs no upstream fetch

- **WHEN** a non-leader replica's refresh tick runs
- **THEN** it performs no upstream model-catalog fetch, regardless of whether it reconciled from the store

#### Scenario: First leader bump is not dropped at startup

- **GIVEN** a replica is starting up
- **WHEN** the model refresh scheduler starts
- **THEN** the global cache-invalidation poller with the `model_registry` callback is already installed, so an immediate leader persist-and-bump reaches followers within the poll bound

#### Scenario: Bump during the startup reconcile window is not dropped

- **GIVEN** a replica is starting up and has recorded the invalidation-poller version baseline
- **AND** a leader persists a changed snapshot and bumps the `model_registry` namespace in the window between the replica's one-shot startup reconcile and the poller's first background tick
- **WHEN** the poller's first background tick runs
- **THEN** it observes the version advanced past the recorded baseline and invokes the reconcile callback, so the replica applies the new snapshot within the poll bound rather than waiting for the non-leader scheduler backstop

#### Scenario: Reviving an expired unchanged snapshot bumps the bus

- **GIVEN** a snapshot was persisted with content hash H and its stored row then aged past `model_registry_snapshot_max_age_seconds`, so followers dropped to the bootstrap floor and reset their applied-content-hash marker
- **WHEN** the leader's next refresh succeeds with the same catalog bytes (content hash H again)
- **THEN** the leader advances `refreshed_at` without rewriting the payload but still bumps the `model_registry` namespace
- **AND** the followers observe the version change and re-apply the revived snapshot within the poll bound rather than waiting for the non-leader scheduler backstop

#### Scenario: Failed startup baseline prime is surfaced, not silently absorbed

- **GIVEN** a replica is starting up and the invalidation-poller baseline-priming read fails transiently
- **WHEN** the priming step runs
- **THEN** the poller remains uninitialized and the failure is surfaced (logged) rather than treated as a recorded baseline, degrading explicitly to first-poll-baseline behavior

### Requirement: Persisted model catalog survives restart and version skew

At startup every replica SHALL load the persisted model-registry snapshot into its in-memory registry before background schedulers start, provided the snapshot's age is within `model_registry_snapshot_max_age_seconds` (default 86400); an older snapshot SHALL be ignored so the bootstrap catalog remains the floor. A replica that still carries local registry state — whether an applied persisted snapshot or an unpublished leader-local refresh whose persist failed (no applied-snapshot marker) — SHALL drop it, reverting to the bootstrap floor and invalidating its local account-selection cache, when a reconcile observes that the only stored snapshot's age now exceeds the cap, so neither an expired catalog nor an unpublished one is served indefinitely while other replicas fall back to the floor. A snapshot whose `schema_version` differs from the running code's codec version SHALL be ignored with a warning and MUST NOT fail startup or the invalidation poller (rolling-deploy safety). A persist failure on the leader SHALL degrade to leader-local refresh behavior with a warning (the in-memory registry is still updated and persistence is retried next cycle) and SHALL reset the replica's applied-snapshot marker, so a later reconcile — for example after losing leadership — reloads the persisted snapshot instead of treating it as already applied. When a reconcile finds no persisted snapshot row at all while the replica still carries local registry state (an unpublished leader-local refresh whose persist failed, or an applied row deleted from the store), the replica SHALL drop that state — reverting to the bootstrap floor and invalidating its local account-selection cache — so it converges with the other replicas until a leader publishes a snapshot. When a leader refresh tick has active accounts but every upstream catalog fetch fails, the leader SHALL reconcile from the store on that tick — it made no change and did not advance the persisted `refreshed_at`, so under a prolonged upstream outage the leader SHALL drop to the bootstrap floor once the stored snapshot's age exceeds the staleness cap (matching the followers) instead of serving its now-stale in-memory catalog indefinitely; while the stored row is still fresh this reconcile SHALL be a no-op. Imported snapshots SHALL preserve refresh-TTL semantics by deriving the monotonic `fetched_at` from the persisted wall-clock `refreshed_at`.

#### Scenario: Restart loads the persisted catalog before the first refresh

- **GIVEN** a fresh persisted snapshot exists
- **WHEN** a replica starts
- **THEN** its registry serves the persisted catalog before the first refresh tick completes

#### Scenario: Snapshot older than the staleness cap is ignored

- **GIVEN** the persisted snapshot's `refreshed_at` is older than `model_registry_snapshot_max_age_seconds`
- **WHEN** a replica starts
- **THEN** the snapshot is not applied and the bootstrap catalog is served
- **AND** the next successful leader refresh repopulates the snapshot

#### Scenario: Mismatched schema version is ignored without error

- **GIVEN** the persisted snapshot's `schema_version` differs from the running code's codec version
- **WHEN** a replica attempts to load it at startup or via the poller
- **THEN** the snapshot is ignored with a warning and no error is raised

#### Scenario: Leader persist failure keeps the leader serving its refreshed catalog

- **GIVEN** the leader's registry refresh succeeded but persisting the snapshot fails
- **WHEN** the refresh cycle completes
- **THEN** the leader's in-memory registry still serves the refreshed catalog and a warning is logged
- **AND** the replica's applied-snapshot marker is reset so reconciliation no longer treats the store's row as already applied

#### Scenario: Former leader converges back to the persisted snapshot after a failed persist

- **GIVEN** a replica applied persisted snapshot hash H, then won leadership, refreshed its in-memory registry, and failed to persist the refreshed state
- **WHEN** the replica loses leadership and its next reconcile runs (poller callback or refresh-tick backstop)
- **THEN** it reloads the store's snapshot H and stops serving the unpublished catalog

#### Scenario: Unpublished catalog is dropped when the store is empty after leadership loss

- **GIVEN** the first-ever leader refresh updated a replica's in-memory registry but persisting the snapshot failed, so no `model_registry_snapshot` row exists
- **WHEN** the replica loses leadership and its next reconcile runs (poller callback or refresh-tick backstop)
- **THEN** it drops the unpublished catalog, reverts to the bootstrap floor, and invalidates its account-selection cache

#### Scenario: Applied snapshot is dropped once the store entry expires

- **GIVEN** a follower applied a persisted snapshot while it was fresh
- **AND** the leader stops advancing `refreshed_at` until the stored snapshot's age exceeds `model_registry_snapshot_max_age_seconds`
- **WHEN** the follower's next reconcile runs
- **THEN** the follower drops the applied snapshot, reverts to the bootstrap catalog floor, and invalidates its account-selection cache

#### Scenario: Leader drops the stale catalog when all fetches fail past the staleness cap

- **GIVEN** the leader applied a persisted snapshot while it was fresh
- **AND** every subsequent leader refresh tick has active accounts but all upstream catalog fetches fail, so the persisted `refreshed_at` is never advanced and its age exceeds `model_registry_snapshot_max_age_seconds`
- **WHEN** the leader's next refresh tick runs and again fails all fetches
- **THEN** the leader reconciles from the store, drops the stale in-memory snapshot, reverts to the bootstrap catalog floor, and invalidates its account-selection cache (converging with the followers)

#### Scenario: Leader keeps a fresh catalog when all fetches fail within the staleness cap

- **GIVEN** the leader applied a persisted snapshot that is still within `model_registry_snapshot_max_age_seconds`
- **WHEN** a leader refresh tick has active accounts but all upstream catalog fetches fail
- **THEN** the leader keeps serving the applied catalog (the reconcile is a no-op because its applied content hash already matches the store) and does not drop to the bootstrap floor

#### Scenario: Unpublished catalog is dropped when the only stored row is expired after leadership loss

- **GIVEN** a leader refresh updated a replica's in-memory registry but persisting the snapshot failed (no applied-snapshot marker)
- **AND** the only `model_registry_snapshot` row is a previously published snapshot whose age now exceeds `model_registry_snapshot_max_age_seconds`
- **WHEN** the replica loses leadership and its next reconcile runs (poller callback or refresh-tick backstop)
- **THEN** it drops the unpublished catalog, reverts to the bootstrap floor, and invalidates its account-selection cache

## MODIFIED Requirements

### Requirement: Bootstrap model catalog is available before refresh

Before the first successful upstream model-registry refresh, the system MUST
serve a conservative static catalog of known Codex model slugs from both
`GET /v1/models` and `GET /backend-api/codex/models`. This static catalog is a
bundled fallback for startup/offline paths; refreshed upstream model-registry
data remains the authoritative source once available. A replica that starts
while a fresh persisted registry snapshot exists SHALL serve the persisted
catalog (not the bootstrap catalog) before its first scheduler tick; the
bootstrap catalog remains the floor only when no persisted or refreshed
snapshot is available. The bootstrap catalog MUST
include `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.5`,
`gpt-5.4`, `gpt-5.4-mini`, `gpt-5.3-codex`, `gpt-5.3-codex-spark`,
`gpt-5.2`, and `codex-auto-review`, and MUST NOT invent unverified variant
slugs such as `gpt-5.5-pro` or a bare `gpt-5.6`. `gpt-5.3-codex` and
`gpt-5.3-codex-spark` were dropped from upstream's bundled catalog at
codex rust-v0.144.x but remain retained for older pinned clients because the
upstream backend still serves them.

#### Scenario: OpenAI-compatible models endpoint serves bootstrap slugs

- **GIVEN** the model registry has no refreshed upstream snapshot
- **AND** no persisted registry snapshot is available
- **WHEN** a client calls `GET /v1/models`
- **THEN** the response contains exactly the bootstrap model slugs
- **AND** the response includes `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna`
- **AND** the response does not include `gpt-5.5-pro` or bare `gpt-5.6`

#### Scenario: Codex-native models endpoint serves GPT-5.6 bootstrap metadata

- **GIVEN** the model registry has no refreshed upstream snapshot
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** the `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna` entries include representative upstream metadata including context-window, visibility, speed-tier, and reasoning fields
- **AND** Sol and Terra advertise `low`, `medium`, `high`, `xhigh`, `max`, and `ultra`
- **AND** Luna advertises `low`, `medium`, `high`, `xhigh`, and `max`

#### Scenario: Replica startup with a fresh persisted snapshot serves the persisted catalog

- **GIVEN** a fresh persisted registry snapshot exists whose catalog differs from the bootstrap catalog
- **WHEN** a replica starts and a client calls `GET /v1/models` before the first refresh tick
- **THEN** the response reflects the persisted catalog, not the bootstrap catalog
