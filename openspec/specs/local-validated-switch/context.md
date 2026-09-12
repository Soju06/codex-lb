# Local entry operations

Run `scripts/local_stable_entry.py --directory <private-dir> --listen 2457 --validator <python> <absolute-path>/scripts/local_entry_acceptance.py`.
Create the directory mode 0700 and `state.json` containing `{"port":2455}` first. The entrance binds loopback only; `control.sock` is mode 0600. It survives backend changes and does not run migrations, restart services, retry inference, or shut down backends.

Send one JSON line to the Unix socket: `{"action":"status"}` or `{"action":"switch","port":2456}`. A successful switch requires health, catalog, and the fixed validator command to pass at the exact destination port. The bundled validator exercises HTTP streaming and WebSocket using a retained tool-result fixture and TRAE Luna. It consumes a small amount of inference quota. Failed gates leave routing unchanged. Return to the previous port using the same switch operation; rollback is also validated.

Provision each backend independently before switching. Keep the previous process running until its connection count is zero. Long-lived HTTP keep-alive and WebSocket connections remain pinned; zero new traffic does not imply drained connections. The entry does not autonomously fail over existing requests. Its own restart interrupts connections and is a separate maintenance operation.

The database must already be compatible with both running versions. Do not start candidates that auto-migrate a shared production database without first verifying their migration heads and backwards compatibility on a snapshot. This router cannot certify schema compatibility or recover context encrypted by a different provider. The bundled probe is not a full real-task acceptance suite; extend the fixed operator-owned validator with tool roundtrips and task fixtures for each rollout.

Initial migration: stage the entrance on a new port pointing at the existing backend. This leaves the original listener intact. Moving the entrance to occupied port 2455 needs a separately coordinated handover after existing connections drain; it cannot be done by restarting the sole backend first. Client routing must remain unchanged until the user requests enabling the validated entrance.

## SQLite validation result

The deployed application holds `store.db.runstate.lock` for its lifetime. A real experiment starting two schema-compatible releases against one disposable production snapshot failed with `SqliteRunStateLockError` in the second instance. Do not bypass or delete this lock. Online overlap against SQLite is unsupported by this deployment, regardless of revision matching.

`python -m scripts.local_database_gate --database <sqlite-file> --active <release-dir> --candidate <release-dir>` performs read-only schema checks. It rejects changed heads, migration contents, or ORM definitions. Passing it is necessary but insufficient for overlapping processes.

Set `LOCAL_ENTRY_DATABASE_PLAN` on the acceptance validator to an operator-owned JSON file with `database`, `active_release`, `active_port`, and `candidates` (port strings mapped to release directories). SQLite plans deliberately refuse switching away from `active_port`. Use a separately validated PostgreSQL migration for overlapping instances or a coordinated SQLite maintenance window. Production migrations and client reconfiguration are separate operations.

## PostgreSQL migration and acceptance

`scripts/local_postgres_import.py` reads the URL from `LOCAL_POSTGRES_URL` (a synchronous `postgresql+psycopg` URL) and accepts `--source <sqlite-file> --report <new-report.json>`. Load the URL from a private operator file; do not put credentials in shell history or argv. Reports contain table counts and SHA-256 hashes, not row contents. The importer never migrates schema, clears a target, or changes client routing. It refuses existing application rows. PostgreSQL sequence changes are not transactional; only an unused target is permitted, and sequences are reset after row verification. A failed transaction never leaves partial application rows.

Prepare a new database using the exact active release's migration graph. Its migrations may seed singleton settings, invalidation and rollup-state rows. Before first import, inspect and clear these only in the newly provisioned, unused target. Do not generalize this step into truncating a configured service database. Do not combine SQLite→PostgreSQL conversion with an unrelated upstream schema upgrade.

A PostgreSQL plan has `driver: "postgresql"`, `active_release`, `candidates`, `connection_file` (private JSON containing `url`), `encryption_key_file`, and `backend_environments` (port→private environment-JSON file). Each backend environment declares `CODEX_LB_DATABASE_URL`, `CODEX_LB_DATABASE_MIGRATE_ON_STARTUP: "false"`, the shared `CODEX_LB_ENCRYPTION_KEY_FILE`, and a distinct `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_ID`. The gate checks these operator records; the operator remains responsible for starting the actual processes from those records. Keep the launch configurations and environment records in sync. Both asyncpg and psycopg URLs are compared as the same database identity.

For a local pair, use distinct IDs such as `pg-blue` / `pg-green`, a shared `pg-blue,pg-green` instance ring, leader election, and advertised endpoints `http://pg-blue.localhost:2461` / `http://pg-green.localhost:2462`. Verify that both hostnames resolve locally before startup and that membership metadata contains the exact endpoints. This macOS host resolves both to loopback without modifying hosts files. Metrics ports must also be distinct. Preserve the original encryption key; do not generate one for the new database.

Run `LOCAL_ENTRY_DATABASE_PLAN=<private-plan> python -m scripts.local_postgres_acceptance --blue 2461 --green 2462` against the prepared pair. It starts an isolated TCP entrance, validates HTTP/SSE and WebSocket inference, switches A→B→A, checks that the retained WebSocket completes two turns while new connections go to B, and waits for connections to drain. It does not modify the production entrance. A provider timeout fails acceptance; a green `/health` alone cannot pass the gate. This verifies bounded synthetic inference and connection behavior, not every long-running user task or every provider.

## Initial cutover procedure

1. Complete the import and pair rehearsal first. Prepare an empty final PostgreSQL schema and two unloaded launch configurations. Preserve the SQLite launch configuration, entry plan, database and encryption key. Inspect current client routes, direct backend connections, entry connection counts and active inference.
2. Coordinate the initial SQLite maintenance window. A consistent final copy requires stopping source writers; the rehearsal snapshot cannot be promoted while the source continues to change. The browser may have direct keep-alive connections even when Codex is using its official endpoint.
3. After requests drain, disable the old LaunchAgent so it cannot respawn, then make a final SQLite backup and import into the unused final PostgreSQL database. Preserve original files. If import fails before any PostgreSQL backend starts, restore the original LaunchAgent and leave entry routing unchanged.
4. Start both PostgreSQL launch configurations with migrations disabled. Verify health, catalog, database plan and actual inference. Once these backends begin writing, recovery must retain their database: do not silently switch to stale SQLite, especially after credentials refresh. On failure, keep the original files for diagnosis and recover the PostgreSQL deployment or explicitly reconcile newer data.
5. Atomically install the PostgreSQL validator plan and request a validated entrance switch to the accepted backend. The entrance remains on port 2457. Keep both PostgreSQL backends available for subsequent connection-preserving switches. If retaining a compatibility listener on 2455, provision and verify it separately after the old process releases that port.
6. Confirm PostgreSQL sessions, shared ring membership, active destination, model calls and unchanged Codex routing. Enabling Codex's endpoint is a separate user-requested action. Back up PostgreSQL with `pg_dump` and retain the original SQLite snapshot; neither is a substitute for tested restore.

The local Docker container is bound to loopback with a dedicated persistent volume and `unless-stopped` restart policy. PostgreSQL availability still depends on the local Docker/Colima VM; the restart policy does not itself start Colima after a macOS reboot. Database migration solves shared-writer locking, not upstream provider outages or host failure.

## Local deployment receipt (2026-09-12)

The approved initial migration completed with 59 tables and 107,222 rows verified from a stopped SQLite source. `local.codex-lb-pg-blue` (2461) and `local.codex-lb-pg-green` (2462) now share PostgreSQL; the original SQLite LaunchAgent is disabled. `local.codex-lb-entry` remains on 2457, with blue active. `local.codex-lb-compat` preserves port 2455 by forwarding to 2457. Start that compatibility listener only after the stable entry points to a backend, otherwise the old 2457→2455 route would form a loop.

Both the isolated pair and the final public-port HTTP/WebSocket acceptance passed. An isolated restore of the PostgreSQL backup recovered all 59 tables. Private receipts and original files live under `~/.codex-lb/postgres/cutover`. The client config checksum is unchanged; this deployment does not re-enable the Codex endpoint. Future backend rollbacks stay on the shared PostgreSQL database and require schema/inference acceptance. SQLite is an original recovery snapshot, not a current rollback destination after new PostgreSQL writes.

The initial window took 178 seconds to switch the entry. Cold Python imports under LaunchAgent were slower than foreground subprocess imports. Rehearse the exact LaunchAgent environment and await actual readiness before inference; do not assume that foreground startup duration predicts background startup, and never proceed to cutover after a readiness deadline expires.
