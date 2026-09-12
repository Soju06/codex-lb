# Local verification, 2026-09-12

- PostgreSQL 18 Alpine staged on loopback 55432 with a dedicated Docker volume. Docker Hub timed out; the existing mirror path supplied the image pinned by content digest. Credentials are private local files, outside Git.
- Exact production release schema (`20260910_190000_company_source_budget`) was independently migrated into PostgreSQL. No production schema revision was changed.
- Rehearsal snapshot: 59 tables, 107,022 rows; canonical row counts and hashes matched before commit. Migration-seeded singleton rows in the unused target were inspected and cleared before import.
- Two backends on 2461/2462 concurrently wrote dashboard settings ten times and passed actual HTTP/SSE and WebSocket TRAE Luna calls. Initial upstream response-header timeout was rejected; subsequent acceptance succeeded.
- Isolated entrance validated A→B→A. Original WebSocket completed two turns after switch, new connection stayed on B through rollback, and all connections drained. An extra operator query initially used the wrong ring metadata column; corrected readback confirmed distinct advertised loopback endpoints. This did not affect the successful acceptance harness.
- Final database schema and launch configurations are prepared privately under `~/.codex-lb/postgres/cutover`; they are not serving. The original SQLite service and production entrance are unchanged. Codex's endpoint remains commented out.
- Initial maintenance window remains pending because final source writers must be stopped before import. Do not mark this operational migration complete or archive the change until cutover is verified.
- Validation: 14 focused tests passed against a dedicated real PostgreSQL test database; Ruff and ty passed. OpenSpec 1.13 validated the change, the owning capability and all 66 repository specs. The older locally cached OpenSpec 1.3 rejected existing specs; validation was repeated with the current cached 1.13 CLI.
