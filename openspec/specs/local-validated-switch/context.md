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
