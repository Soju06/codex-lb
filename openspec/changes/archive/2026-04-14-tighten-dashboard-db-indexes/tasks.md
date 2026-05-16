## 1. latest usage query shape 개선

- [x] 1.1 PostgreSQL `latest_by_account()`를 index-friendly latest-row query로 전환한다.
- [x] 1.2 SQLite 경로는 기존 semantics를 유지한다.
- [x] 1.3 PostgreSQL integration test를 새 query shape 기준으로 갱신한다.

## 2. dashboard read indexes 추가

- [x] 2.1 `usage_history` normalized window + account + time composite index를 추가한다.
- [x] 2.2 `request_logs` aggregation/facet workload용 composite indexes를 추가한다.
- [x] 2.3 `api_keys.name` 검색 보조 인덱스를 추가한다.
- [x] 2.4 Alembic migration downgrade 경로를 포함한다.

## 3. 검증

- [x] 3.1 migration/integration tests에서 새 인덱스가 head schema에 포함됨을 검증한다.
- [x] 3.2 관련 pytest 스위트와 OpenSpec validation을 통과시킨다.
