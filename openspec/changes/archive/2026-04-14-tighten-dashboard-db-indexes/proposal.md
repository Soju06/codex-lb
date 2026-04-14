## Why

대시보드가 무거운 codex-lb 트래픽과 함께 느려질 때, `usage_history`와 `request_logs`의 hot query들이 기존 인덱스를 충분히 활용하지 못하는 구간이 확인됐다. 특히 PostgreSQL에서 `latest_by_account()`는 normalized window 인덱스가 있어도 seq scan + external sort를 선택했고, dashboard request-log 집계/옵션 쿼리는 최근 구간을 반복 스캔한 뒤 정렬/집계를 수행했다.

## What Changes

- PostgreSQL의 `UsageRepository.latest_by_account()`를 backend-specific latest-row query로 조정하여 normalized window latest index를 직접 활용한다.
- dashboard request-log 집계와 facet/filter workload에 맞는 composite indexes를 추가한다.
- usage history의 time-range bulk/history reads에 맞는 normalized window composite index를 추가한다.
- Alembic migration과 migration/integration tests를 함께 갱신한다.

## Impact

- **Code**: `app/modules/usage/repository.py`, `app/db/models.py`
- **DB**: 신규 Alembic revision으로 `usage_history`, `request_logs`, `api_keys` 인덱스 추가
- **Behavior**: API 응답 스키마 변경 없음, dashboard/read-path DB 계획만 최적화
- **Tests**: PostgreSQL query-plan assertion과 migration index assertions 강화
