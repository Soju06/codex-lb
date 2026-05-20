## 1. Runtime hardening

- [x] 1.1 Add bounded retry/backoff for transient SQLite lock errors around usage reservation writes
- [x] 1.2 Add bounded retry/backoff or serialization for bridge/ring heartbeat writes when SQLite is selected
- [x] 1.3 Keep retry budgets bounded by the existing request and liveness timing constraints

## 2. Deployment profile

- [x] 2.1 Document a SQLite-conservative Docker profile that keeps SQLite and limits request workers
- [x] 2.2 Keep PostgreSQL documented as the recommended profile for sustained multi-worker throughput
- [x] 2.3 Update `.env.example` or direct Docker examples so the SQLite and PostgreSQL profiles are visibly distinct

## 3. Verification

- [x] 3.1 Add regression coverage for retrying transient SQLite `database is locked` failures on write-hot paths
- [x] 3.2 Validate OpenSpec artifacts
