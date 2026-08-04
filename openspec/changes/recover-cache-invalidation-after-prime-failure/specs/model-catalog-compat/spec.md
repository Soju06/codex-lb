## MODIFIED Requirements

### Requirement: Refreshed model catalog is replica-coherent

The leader refresh cycle SHALL persist the complete registry state (models, plan maps, per-account tier maps, suppression set, authoritative flags, metadata retention state, and the refresh wall-clock timestamp) to the single-row `model_registry_snapshot` table and SHALL bump the `model_registry` cache-invalidation namespace only after the persist commits (write-then-bump). The payload write and the bump SHALL be skipped when the serialized content hash is unchanged from the last persisted state AND the stored row was still within `model_registry_snapshot_max_age_seconds`; the stored `refreshed_at` timestamp SHALL still be advanced so snapshot age reflects the leader's latest successful refresh. When the content hash is unchanged but the stored row had already aged past `model_registry_snapshot_max_age_seconds` before this refresh revived it, the leader SHALL still bump the `model_registry` namespace (only the payload rewrite stays skipped): an expired row causes followers to clear their local registry and reset their applied-content-hash marker, so an unchanged-content revival still requires a bump for them to re-apply within the cache-invalidation poll bound instead of waiting for the non-leader scheduler backstop. Every replica MUST apply a newly persisted snapshot within the cache-invalidation poll bound and MUST invalidate its local account-selection cache on apply; that account-selection invalidation MUST be local-only (non-propagating), because reconcile only applies a change the leader already published (which bumped `model_registry` to reach every replica) and each replica clears its own selection cache on apply, so a propagating clear would make every follower durably re-bump `account_selection` and amplify bus traffic with no peer-visible effect. When the reconcile is driven by the `model_registry` invalidation callback and the snapshot load fails (transient DB read error or malformed payload), the callback MUST surface the failure to the invalidation poller so the poller leaves the `model_registry` version unacknowledged and retries on the next poll cycle (matching the `account_routing` refresh callback), rather than acknowledging the bump and stranding the replica on the stale catalog until the non-leader scheduler backstop; the startup one-shot reconcile and the refresh-tick backstop instead swallow such a load failure (keeping the current in-memory state) so they never fail startup or the scheduler loop. Payload decode MUST treat a set-backed or mapping-backed catalog field whose persisted value has the wrong type — for example a `model_plans`/`plan_models`/`model_accounts`/per-account tier entry persisted as a scalar or object where a list of slugs is expected, or a model entry that is not an object — as a malformed payload and raise, rather than silently dropping the offending entry and applying a partial catalog; a genuinely-absent or empty container (an absent key, an empty map, or an empty list) is not malformed and MUST decode successfully. After apply, `/v1/models`, plan gating (`plan_types_for_model`), suppression (`is_suppressed_model`), and per-account service-tier routing on a non-leader MUST be identical to the leader. A non-leader refresh tick MUST NOT fetch the upstream catalog and SHALL instead reconcile from the persisted snapshot when the stored snapshot header differs from the last applied one (backstop for a lost invalidation bump). A leader catalog clear SHALL persist an explicit cleared marker and bump, so followers revert to the bootstrap floor rather than serving a withdrawn catalog. Every replica SHALL install its `model_registry` cache-invalidation callback (the global invalidation poller) before starting the model refresh scheduler, so a first leader tick that persists a changed snapshot cannot silently drop its bump. Every replica SHALL record the invalidation-poller version baseline before running its one-shot startup reconcile, so a leader bump that lands in the window between that reconcile's snapshot read and the poller's first background tick is delivered as an invalidation callback (within the poll bound) rather than absorbed as the poller's initial callback-less baseline (which would defer convergence to the non-leader scheduler backstop). The baseline-priming read SHALL surface a failure to its caller and leave the poller without a recorded baseline. If startup continues, the first successful background poll MUST conservatively treat a positive `model_registry` version observed without a baseline as changed, invoke the reconcile callback, and acknowledge it only after that callback succeeds. This MAY replay a pre-startup version, but MUST NOT absorb a peer bump as a callback-less baseline.

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

#### Scenario: Failed startup baseline prime recovers through reconciliation

- **GIVEN** a replica's baseline-priming read fails transiently and no `model_registry` version baseline is recorded
- **WHEN** its first successful background poll observes a positive `model_registry` version
- **THEN** the poller MUST invoke the model-registry reconcile callback before acknowledging that version
- **AND** the replica MUST NOT defer convergence to the scheduler backstop merely because startup baseline priming failed
