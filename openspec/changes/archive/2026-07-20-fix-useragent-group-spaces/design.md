## Context

`request_logs.useragent_group` is derived from the inbound `User-Agent` by shared proxy request-log parsing. The current parser prematurely truncates at the first whitespace-delimited token, so `Codex Desktop/...` is grouped as `Codex` instead of `Codex Desktop`. This affects new HTTP/WebSocket request logs and historical report buckets. The change also needs a data-only Alembic revision that works on both supported relational backends and does not attempt to reconstruct obsolete derived values on downgrade.

## Goals / Non-Goals

**Goals:**

- Make the shared parser derive a stable group from `useragent.strip()` and the content before the first `/`.
- Preserve existing full nonblank user-agent storage behavior while mapping missing or blank input to null metadata.
- Recompute historical request-log groups using the same normalization on SQLite and PostgreSQL.
- Make repeated migration execution harmless and document the irreversible no-op downgrade.

**Non-Goals:**

- Changing user-agent case, token contents, or the existing stripping behavior for stored nonblank headers.
- Adding a new parser abstraction, dashboard behavior, index, or configuration setting.
- Reconstructing previous historical group values during downgrade.

## Decisions

### Use the shared parser as the single normalization point

The parser will first trim the inbound header for blank detection and storage, then take the content before the first `/` from the trimmed value. If no slash exists, the entire trimmed value is the group. This keeps HTTP and WebSocket request-log paths consistent and avoids caller-specific fixes. The supplied Codex Desktop value `Codex Desktop/0.142.4 (Mac OS 26.5.2; arm64) unknown (Codex Desktop; 26.623.70822)` therefore groups as `Codex Desktop` rather than the prematurely truncated `Codex`.

An alternative was to strip whitespace at each request-log call site; that was rejected because it duplicates behavior and leaves sibling paths vulnerable. Another alternative was to normalize only in report queries; that was rejected because persisted groups remain inconsistent and migration/history consumers would still see bad values.

### Use set-based backend-aware data migration

The Alembic revision will derive groups from the stored `request_logs.useragent` values using the first-slash position and trimming. It will set groups to null for null or blank user-agent values and update existing nonblank rows. The revision must be placed after the current migration head at implementation time, using the repository's normal revision naming and head checks. SQLite and PostgreSQL may use backend-specific set-based SQL branches, provided they produce the same canonical results.

A Python row-by-row migration was considered, but set-based SQL branches keep the migration bounded to one database operation per backend and avoid loading request-log history into application memory. A single identical SQL expression across backends is not required; only the resulting normalized values must match.

### Keep downgrade a no-op

`useragent_group` is derived data and the prior incorrectly spaced values are not recoverable from the normalized result. The downgrade will intentionally perform no data mutation, while the revision remains structurally reversible in Alembic. This is safer than inventing old values or clearing valid normalized history.

### Keep regression coverage focused

One shared-parser regression test will cover the supplied Codex Desktop user-agent and assert the exact group. Migration regression coverage will exercise historical recomputation on SQLite, with PostgreSQL validation only when the repository's migration test setup provides it, including a second upgrade/application check for idempotence and the no-op downgrade.

## Risks / Trade-offs

- **[Risk]** A large historical `request_logs` table can make the data update expensive. **Mitigation:** use one set-based update per backend, run it as part of the existing Alembic deployment process, and verify the revision on both backends when available.
- **[Risk]** SQL string-position semantics differ between SQLite and PostgreSQL. **Mitigation:** use backend-specific set-based expressions where needed, validate their results against the same canonical examples, and run available PostgreSQL validation.
- **[Risk]** Downgrade cannot restore old derived values. **Mitigation:** make the no-op behavior explicit and avoid claiming reversibility of data contents.

## Migration Plan

1. Add the parser regression test and shared parser normalization.
2. Add the Alembic data revision with the current head as its parent.
3. Run the required migration regression coverage on SQLite; run PostgreSQL validation when the project harness provides it, verify repeated upgrade/application is harmless, and verify downgrade leaves normalized data unchanged.
4. Deploy normally through Alembic upgrade-to-head. Rolling back code does not restore old derived groups; a later forward migration can reassert the canonical derivation if required.

## Open Questions

None. The migration parent revision is resolved from the repository's current Alembic head when implementation begins.
