## MODIFIED Requirements

### Requirement: Overflow and transport migration heads converge without rewriting history

The migration graph MUST join `20260908_000000_add_subscription_overflow` and
`20260908_000000_replace_upstream_stream_transport_default_sentinel` through a
new merge revision. Both existing revisions MUST remain unchanged. The merge
revision's upgrade and downgrade MUST NOT execute application schema or data
operations.

Every claim this requirement makes about preserved rows and restored stamps
MUST be read against the merge revision
`20260908_020000_merge_overflow_transport_heads`, which is the revision under
test, and MUST NOT be read against `head`. The merge revision was head when it
landed and is an interior node now; a later revision MAY retire schema that
either parent created, so a walk that continues past the merge MUST NOT be
taken to promise that the parents' rows survive to `head`.

#### Scenario: An existing parent upgrades to the merged head

- **GIVEN** a populated database at either parent, or at both parents
- **WHEN** the normal migration runner upgrades to
  `20260908_020000_merge_overflow_transport_heads`
- **THEN** it MUST apply any missing parent according to that parent's existing
  behavior and finish stamped at that single merge revision
- **AND** it MUST preserve existing application rows except for data changes
  already required by a missing parent's migration
- **AND** the resulting schema MUST match what both parents build

#### Scenario: Downgrading only the merge preserves both parents

- **GIVEN** a populated database at the merge revision
- **WHEN** Alembic downgrades to either immediate parent
- **THEN** it MUST undo only the merge revision and retain both parent revision
  stamps and both parent schemas
- **AND** application data MUST remain unchanged
- **AND** upgrading to the merge revision again MUST restore the single merge
  stamp without repeating either parent's schema or data operations

#### Scenario: Continuing to head applies later retirements

- **GIVEN** a populated database stamped at the merge revision
- **WHEN** the runner continues to `head`
- **THEN** every revision above the merge MUST apply according to its own
  contract, including any that retires schema a parent created
- **AND** the resulting schema MUST match the current ORM metadata

## ADDED Requirements

### Requirement: Withdrawn overflow storage is retired without stranding an intermediate install

`20260914_000000_drop_subscription_overflow_schema` MUST remove the storage the
withdrawn subscription-exhaustion overflow feature left behind: the
`model_source_pins` table with both of its indexes, and the
`dashboard_settings` columns `subscription_overflow_source_id` and
`subscription_overflow_drain_until`. The revisions that built them
(`20260908_000000_add_subscription_overflow`,
`20260911_000000_model_source_pins_kind_expires_index`) MUST remain in the
graph unchanged, because an install stopped below them upgrades through them
before reaching this one, and the graph MUST keep a single head.

Dropping stored pins is authorized and MUST NOT be backfilled or exported. A
pin was routing state — which model source a conversation or API key had been
handed to, under an idle TTL, a tombstone grace and a drain window — and the
router that wrote and read it was removed in the same withdrawal, so no
surviving code path can consume a retained row.

Every step MUST be individually guarded against the object being absent, so a
partial dump, a database restored from a mixed backup, or a fresh install
created from current ORM metadata upgrades cleanly rather than failing on a
missing table or column.

The downgrade MUST restore exactly what the two original revisions built, so
the pair round-trips: both settings columns, and the table with its primary
key, its deliberate absence of a foreign key on `source_id`, and both indexes.
Index restoration MUST be reflected independently of table creation. A
downgrade interrupted between `CREATE TABLE` and its indexes, or a restore that
already carries the table alone, MUST still end with both indexes present;
riding the index steps along with table creation would strand such a database
stamped at the parent with no index and no later step to build one.

This upgrade MUST NOT be presented as rolling-safe. Every release below the
retirement maps both settings columns and loads the settings row as one entity,
so a replica of such a release that is still serving when the drop commits fails
every settings read; the chart's migration Job runs as a `pre-upgrade` hook,
which fires before the new pods roll and therefore before the old ones drain.
The operator documentation MUST require stopping or scaling every
pre-retirement replica to zero before this migration runs, MUST name the two
dropped settings columns, and MUST state that a rolled-back replica MUST NOT be
started while the drop is in flight.

#### Scenario: Install below the overflow revisions upgrades straight to head

- **GIVEN** a populated database stamped below
  `20260908_000000_add_subscription_overflow`
- **WHEN** the runner upgrades to `head`
- **THEN** each overflow revision MUST build its own objects as it is applied
- **AND** the retirement revision MUST then drop the table, both indexes and
  both settings columns
- **AND** the final schema MUST match the current ORM metadata with no drift

#### Scenario: Missing objects do not fail the upgrade

- **GIVEN** a database at the retirement revision's parent that is missing
  `model_source_pins`, or is missing either settings column
- **WHEN** the runner upgrades to `head`
- **THEN** the revision MUST skip the absent object and apply the rest
- **AND** the upgrade MUST NOT raise

#### Scenario: Downgrade rebuilds the retired schema

- **GIVEN** a database stamped at the retirement revision
- **WHEN** Alembic downgrades to its immediate parent
- **THEN** both settings columns MUST exist again as nullable columns
- **AND** `model_source_pins` MUST exist with `pin_key` as its primary key, no
  foreign key on `source_id`, and both
  `ix_model_source_pins_purge_at` and `ix_model_source_pins_kind_expires_at`

#### Scenario: Downgrade repairs a table left without its indexes

- **GIVEN** a database stamped at the retirement revision whose
  `model_source_pins` table is already present but carries neither index — an
  interrupted earlier downgrade, or a partial restore
- **WHEN** Alembic downgrades to the immediate parent
- **THEN** the revision MUST skip table creation and MUST still create both
  missing indexes
- **AND** the restored schema MUST match the revision it downgraded to

#### Scenario: Operator documentation requires the stop before the drop

- **GIVEN** the Kubernetes deployment guide, whose upgrade path runs the
  migration Job as a `pre-upgrade` hook
- **WHEN** an operator upgrades from any release below the retirement
- **THEN** the guide MUST tell them to stop or scale every pre-retirement
  replica to zero before the migration runs, or to run the migration by hand
  once the old colour is stopped
- **AND** it MUST name `dashboard_settings.subscription_overflow_source_id` and
  `subscription_overflow_drain_until` as the columns whose removal breaks an
  earlier release's settings reads
- **AND** it MUST state that no window is supported in which a pre-retirement
  pod runs against the post-drop schema
