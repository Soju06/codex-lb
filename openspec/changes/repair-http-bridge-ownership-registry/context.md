# Context

The `20260901_000000_repair_persisted_schema_drift` revision only called
`ensure_ownership_table` while creating the historical HTTP bridge index.  A
database where that index already existed could therefore reach the repair
revision without the ownership table required by ORM metadata.  The new
revision restores that missing additive table before startup drift checking.
