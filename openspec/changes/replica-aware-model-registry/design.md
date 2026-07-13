# Design: Replica-aware model registry

## Decisions

1. **Persistence target: new table `model_registry_snapshot`** — columns: `id` Integer PK (always 1), `schema_version` Integer NOT NULL, `content_hash` String(64) NOT NULL, `payload` Text NOT NULL (canonical JSON), `refreshed_at` DateTime(tz) NOT NULL (leader wall clock at refresh), `leader_id` String NULL (diagnostics only). Rejected: reusing `model_sources`/`model_source_models` — those model operator-configured OpenAI-compatible endpoints with per-key assignment lifecycle, not the fetched subscription catalog (different shape: per-plan/per-account tier maps, suppression set, authoritative flags, metadata retention); shoehorning would corrupt both contracts.

2. **Write path & locking**: leader-only writer performs a dialect-specific atomic single-row upsert exactly like `CacheInvalidationPoller.bump` — `pg_insert(...).on_conflict_do_update(index_elements=[id])` on PostgreSQL, `sqlite_insert(...).on_conflict_do_update` on SQLite (atomic under SQLite's single-writer lock). No advisory lock and no CAS: leader election already bounds writers to ~1, and because the payload is derived from the same upstream, concurrent writers (leader flap, or SQLite's everyone-is-leader bypass while it exists) are idempotent last-writer-wins; `content_hash` short-circuits duplicate payload writes and prevents bump storms.

3. **Propagation**: reuse the existing `cache_invalidation` version-counter bus + 0.5s `CacheInvalidationPoller` with a new `NAMESPACE_MODEL_REGISTRY`, write-then-bump ordering. Followers read the payload ONLY when the version changes (the 0.5s poll already SELECTs the tiny versions table; zero new steady-state queries). TTL backstop: the non-leader scheduler tick (default every 300s) does a header-only SELECT (`schema_version`, `content_hash`, `refreshed_at`) and reloads on mismatch — covers `bump()`'s documented swallow-on-failure. Rejected: bridge-ring HTTP fan-out (state must survive restarts anyway; bus exists); per-request DB read (hot-path round-trip).

4. **Rejected alternative**: dropping the leadership gate so every replica fetches upstream — N-fold upstream model fetches per interval, and the fetch path calls `AuthManager.ensure_fresh` (force=True on 401), which would widen the known cross-replica refresh-token rotation race handled by the serialize-cross-replica-token-refresh sibling change.

5. **fetched_at fidelity**: `ModelRegistrySnapshot.fetched_at` is `time.monotonic()`-based; the row persists wall-clock `refreshed_at` (derived from `fetched_at` at export), and decode sets `fetched_at = time.monotonic() - max(0, now_utc - refreshed_at)` so `needs_refresh()` and TTL semantics stay correct on the importing process.

6. **Restart/staleness**: every replica loads the persisted snapshot at lifespan startup if `now - refreshed_at <= model_registry_snapshot_max_age_seconds` (new setting, default 86400); older snapshots are ignored (bootstrap floor) and corrected by the next leader refresh. `schema_version` mismatch (rolling deploys) is ignored with a warning, never an error — old-code replicas never read the table, new-code follower + old-code leader degrades to today's behavior.

7. **Clear propagation**: when the leader clears the registry (no active accounts), it persists an explicit `cleared` payload marker and bumps, so followers also revert to the bootstrap floor rather than serving a ghost catalog.

8. **Follower apply also invalidates the local `AccountSelectionCache`**, mirroring the leader's post-refresh invalidate. Cross-replica `SelectionInputs` staleness in general belongs to the extend-cache-invalidation-bus sibling; here we only mirror the registry-driven invalidate.

9. **Migration parent**: revision `20260712_070000_add_model_registry_snapshot` chains after the current committed head `20260711_030000_add_limit_warmup_idle_threshold`. Sibling branches in this effort add migrations on the same head (timestamps `20260712_0*`); the cross-branch head fork is resolved at merge time — re-parent (or add a merge revision) before merge so CI sees a single head. Downgrade drops the table. Plain `CREATE TABLE` with a Text payload — no JSONB dependency, so SQLite and PostgreSQL share one path.

10. **Performance**: hot proxy path untouched (all registry reads stay in-memory). Added load: leader — one small upsert per refresh cycle only when the catalog actually changed (plus a tiny `refreshed_at` touch UPDATE when unchanged); followers — one payload SELECT per catalog change, plus one header-only SELECT per refresh tick.

## Deviations from the reviewed design

- **Migration parent**: the reviewed design said to chain after the (uncommitted, other-branch) `20260712_010000_add_account_usage_rollups`; that migration does not exist on this branch, so the revision parents on the committed head `20260711_030000_add_limit_warmup_idle_threshold` with a distinct `20260712_070000` timestamp. Re-parenting/merge-revision is a merge-time follow-up.
- **`refreshed_at` touch on unchanged content**: the design said "persist is skipped when content_hash is unchanged". Skipping entirely would let a *stable* catalog age past the staleness cap even while the leader confirms it every tick (refresh interval 300s vs cap 86400s), making every restarted replica ignore a perfectly current snapshot. The payload write and the bump are still skipped, but `refreshed_at`/`leader_id` are touched (guarded by `WHERE content_hash = :hash`) so snapshot age means "time since the leader last confirmed this catalog".
- **Staleness cap applies to every store read** (startup load, poller callback, tick backstop), not only startup. This keeps one code path and cannot regress the bus path: a bump immediately follows a leader persist, whose `refreshed_at` is fresh by construction.
- **Reconcile/startup wiring is gated on `model_registry_enabled`**: when the operator disabled the model-registry refresh feature, replicas keep today's bootstrap-only behavior instead of consuming another deployment's snapshots (also keeps the test environment inert).
- **Main-spec sync**: `openspec/specs/model-catalog-compat/spec.md` is NOT updated in this change; the delta lands via the openspec-sot-sync flow after merge (the local main spec predates the synced SSOT this change's MODIFIED header is written against).

## Failure modes

- Leader persist failure: warning; leader keeps serving its refreshed in-memory catalog; retried next cycle (spec: "Persisted model catalog survives restart and version skew").
- Lost bump (documented `bump()` swallow-on-failure): follower converges within one refresh interval via the tick backstop.
- Corrupt/undecodable payload: warning, snapshot ignored, bootstrap floor retained; never raises into the poller or startup.
- Rolling deploy skew: old-code leader never persists, so new-code followers behave exactly as today until the leader upgrades — no regression, but the fix is not effective mid-rollout.

## Risks

- **Codec drift**: future `UpstreamModel`/snapshot field additions that miss the codec would ship subtly wrong catalogs to followers — mitigated by a field-complete round-trip equality test (fails when a dataclass field is added without codec support; note `UpstreamModel.raw` has `compare=False`, so the test compares `raw` explicitly) and the `SCHEMA_VERSION` bump discipline documented in `context.md`.
- **Payload size**: `base_instructions` (~16.5KB/model) and `model_messages` can push the payload to low MBs on large fleets; reads happen only on catalog change and writes are hash-gated. Payload size is logged on persist.
- **SQLite multi-process** (everyone-is-leader bypass, owned by the harden-scheduler-leader-election sibling): multiple writers upsert the same row — atomic under SQLite's write lock and content-idempotent; N upstream fetches remain until that sibling lands.
