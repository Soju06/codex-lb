# Tasks

## 1. Proposal

- [x] OpenSpec change folder with the backend contract (`add-mysql-mariadb-backend`)

## 2. Backend-neutral prerequisites (own change)

- [ ] Epoch-second widening that is safe on PostgreSQL (online plan) and
      effective on MySQL/MariaDB
- [ ] DDL-policy note: how a released revision may be adjusted, if at all

## 3. Dialect layer and MySQL DDL (own change)

- [ ] `dialect_sql` / `migration_indexes` cover MySQL and MariaDB without
      column-name length heuristics for client-supplied values
- [ ] Fresh MySQL/MariaDB database reaches head with `migration_policy=ok`
- [ ] Test suite reuses the schema per file on both MySQL and MariaDB
- [ ] CI: required `test-mysql` completes in budget; MariaDB leg runs
      non-required (nightly/opt-in)

## 4. request_logs performance indexes (own change)

- [ ] Facet indexes built `CONCURRENTLY` on PostgreSQL with invalid-index repair
- [ ] Index justification cites query evidence, not a single engine's slow log

## 5. Evidence

- [ ] `migration-check-mysql` green on MySQL 8.4 and MariaDB 11.8
- [ ] Request-path smoke on both engines
