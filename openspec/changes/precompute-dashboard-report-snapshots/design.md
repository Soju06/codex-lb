# Background analytical read snapshots

## Purpose and scope
Move dashboard and report aggregation off HTTP requests. Preserve existing quota,
accounting, filters, timezone/DST boundaries and historical availability. This
change does not add averaging controls or alter proxy behavior.

## Ownership and storage
A bounded producer uses an independent scheduler lease (ID 102). Separate owned
dashboard and report tasks use separate database sessions and join on cancellation.
A single additive table stores normalized keys, complete payloads, generation,
freshness and refresh interest. Publication validates lease ownership in the same
transaction; failed work retains the last complete generation.

## Consumer path
At startup the producer registers the three dashboard ranges and UTC 7/30/90-day
report presets. Viewer timezone hints and custom filters register bounded interest.
HTTP handlers authenticate, normalize and select stored data, never invoke a
collector. Overview construction reuses the collected projection. Reports preserve
exact distinct/timing semantics and use existing rollups.

For example, a request for the default UTC seven-day report reads one stored
bundle containing report totals, filter options and pool-wide thread identity.
A different account filter is a different key. A cold key returns a retryable
warming error; it must not be labeled an empty report.

## Failure and compatibility
Readers return same-key last-good data during refresh/failure with metadata; the UI
shows delayed updates without replacing valid data with a blank panel. Queue and
payload limits bound untrusted interest. Inference readiness is independent of
analytical readiness. Dashboard and inference transports retain their current behavior.

The migration is additive, validates preexisting layouts and tolerates interrupted
DDL. Old application code ignores the derived table. A downgrade validates then
removes only this table. Existing accounting and request logs are untouched.

## Measurement and alternatives
Process-local expiring caches do not survive restarts or share work across replicas;
browser prefetch only hides the same request-owned work. Durable background values
trade explicitly reported freshness for bounded request cost. Compare upstream and
candidate on the same seeded database, built UI and browser with fresh contexts;
measure navigation until all expected data panels finish, not first paint, API-only
latency or refresh cadence. Report startup/warmup separately from prepared reads.
