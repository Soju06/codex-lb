## Why

The subscription-exhaustion overflow feature (#2123) was withdrawn. The
preceding revert removed its code, dashboard surface, settings and active
change folder; this change retires the storage it left behind:
`model_source_pins` with its two indexes, and the
`dashboard_settings.subscription_overflow_source_id` /
`subscription_overflow_drain_until` columns.

The withdrawal invalidates a requirement that is already synced into
`openspec/specs/database-migrations/spec.md`. "Overflow and transport migration
heads converge without rewriting history" promises that a populated database at
either parent, upgraded **to `head`**, keeps its application rows and lands on
"the single merge head". Both clauses were written when
`20260908_020000_merge_overflow_transport_heads` *was* head. Twelve revisions
later it is an interior node, and once the retirement revision is in the graph,
walking a populated parent to head deliberately destroys the pin rows the
sentence protects. The requirement is not wrong about the merge — it is wrong
about where the claim is anchored, which is exactly the correction the merge
revision's own test needed: its preservation assertions now anchor at
`20260908_020000_merge_overflow_transport_heads` instead of at `head`.

Leaving it unamended would let the spec promise row preservation that the code
does not implement, which the OpenSpec gate exists to prevent. Deleting the
requirement instead is wrong: the merge revision is still in the graph, both
of its parents must still stay unrewritten, and its no-op upgrade/downgrade is
still a live constraint.

Discarding the stored pins needs saying out loud rather than being inferred
from a diff. Pins were routing state, not user data: a pin recorded which model
source a conversation or API key had been handed to, with a 7-day idle TTL, a
21-day tombstone grace and a 29-day drain window, all reconstructed on demand
by the router that no longer exists. Nothing reads them, nothing can, and no
export, retention rule or audit trail references them. Production measured 0
rows in `model_source_pins` and 0 non-NULL values in both settings columns
before the drop, so the migration cannot lose anything here; a self-hosted
install that did enable overflow loses routing hints for a code path that was
removed one revision earlier.

## What Changes

- The `database-migrations` requirement "Overflow and transport migration heads
  converge without rewriting history" is MODIFIED to anchor both scenarios at
  the merge revision rather than at `head`. The merge-and-parents constraints
  are unchanged; only the target of the walk is named explicitly, so the
  requirement keeps stating a fact that stays true as the graph grows past it.
- A new requirement states the retirement contract: what
  `20260914_000000_drop_subscription_overflow_schema` MUST drop, that the pin
  rows are discarded routing state, that every step MUST be guarded so an
  install missing either object still upgrades, and that the downgrade MUST
  restore the schema built by
  `20260908_000000_add_subscription_overflow` and
  `20260911_000000_model_source_pins_kind_expires_index` — including both
  indexes, reflected independently of the table, so a downgrade interrupted
  between the table and its indexes still converges.
- No code change beyond the revision itself: the ORM models and the settings
  columns were removed in the same PR, and `check_schema_drift` at head reports
  no difference against live metadata.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `database-migrations`: MODIFIED "Overflow and transport migration heads
  converge without rewriting history" — the preservation and stamp claims
  anchor at the merge revision, not at `head`. ADDED "Withdrawn overflow
  storage is retired without stranding an intermediate install" — the drop
  revision's guarded upgrade, its discarded-routing-state authorization, and
  its index-repairing downgrade.

## Impact

- Schema: `model_source_pins` (and `ix_model_source_pins_purge_at`,
  `ix_model_source_pins_kind_expires_at`) dropped;
  `dashboard_settings.subscription_overflow_source_id` and
  `subscription_overflow_drain_until` dropped. Production row counts before the
  drop: 0, 0, 0.
- The three historical revisions stay in the graph unchanged. An install
  stopped below them still upgrades through them and then past them, so their
  own tests keep asserting what each revision builds at its own point.
- Operators running a pre-withdrawal release against a database that has taken
  this revision: the two settings columns are gone, so the old image's settings
  read fails. Stand a rolled-back replica up **before** running the migration,
  as with the legacy dashboard-credential drop.

Follow-up to the overflow withdrawal (#2123).
