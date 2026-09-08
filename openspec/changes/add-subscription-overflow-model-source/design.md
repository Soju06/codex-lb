## Context

Design source: issue #2123 (subscription-exhaustion overflow to a designated model source; design §3 constants, §9.1 pin schema, §12 zero-cost-when-disabled budget, §14/§16 rollout). The rollout is staged — schema (this stage), inert settings designation and preflight, pin repository, routing, dashboard, docs — so the Alembic graph stays single-headed while the behavioural stages are reviewed. Every later stage extends this change folder.

## Decisions

1. **`dashboard_settings.subscription_overflow_drain_until` is a naive `DateTime`.** Every `dashboard_settings` timestamp is naive UTC and the application compares them with `utcnow()` (naive UTC). The drain deadline is arithmetic on that clock (`now + 7 d pin idle TTL + 21 d tombstone grace + 1 d = now + 29 d`). Startup drift detection compares `timezone=` exactly, so ORM and DDL both declare it naive.
2. **`model_source_pins` timestamps are `DateTime(timezone=True)`.** Pins follow `file_account_pins`: the database clock is authoritative for expiry and tombstone comparisons, so the pin repository (later stage) can use dialect-native `now()` clauses like `FileAccountPinRepository`.
3. **No foreign key from `source_id` to `model_sources`, no `Enum` for `kind`.** Rows must outlive a deleted source for the drain window instead of cascading away, and `kind` (`thread` | `anchor` | `bounce`) is a namespaced discriminator whose values are owned by the routing stage, not the schema.
4. **No foreign key from `subscription_overflow_source_id`.** Mirrors `single_account_id`: a dangling id is "off" by definition. Deleting the designated source clears the setting in the same transaction (settings stage).
5. **One plain index on `purge_at`, created inside the migration transaction.** The table is created empty by the same revision, so `CONCURRENTLY` buys nothing. The index step is guarded independently of the table step so a pre-existing table (partial dump, out-of-band creation) still receives the index; on PostgreSQL an existing invalid same-named index is dropped and rebuilt rather than accepted by name.
6. **No `server_default`, no backfill, no seed rows.** Both settings columns are nullable and NULL means off / no drain armed; the pin table starts empty.

## Alternatives considered

- Folding the schema into the settings/dashboard stage: rejected — re-parenting a migration behind a long-lived feature branch is what stalled #1664.
- Cascading `source_id` deletes: rejected — pins must keep answering lookups during the drain window.
- Partial unique indexes or a request-log column for pins: rejected by the design (§9.1); `pin_key` namespacing carries the uniqueness.

## Risks

None at runtime in this stage: no code path reads the new columns or table, so the request path cost stays at the one warm `SettingsCache.get()` already performed. Downgrade drops the index, the table, and both columns; the pin table is empty and the settings columns are NULL until later stages write them.
