## Design Summary

단일 릴리스 hard-cut으로 Alembic으로 전환하되, 사용자 DB의 과거 이력을 자동 흡수하기 위한 bootstrap 단계를 포함한다.

### 1) Startup Flow

1. SQLite 경로/무결성 검증 수행
2. migration 필요 상태(`needs_upgrade`)를 진단
3. SQLite + migration 필요 시 pre-migration 백업 생성
4. `app.db.migrate.run_startup_migrations()` 호출
3. bootstrap:
- `alembic_version` 존재 시 skip
- `schema_migrations` 존재 시 연속 prefix를 계산해 대응 revision으로 `stamp`
4. `upgrade head` 수행

### 2) Revision Chain

- `000_base_schema` + 기존 001~008 의미를 보존한 Alembic revision 체인
- 모든 revision은 idempotent 검사(table/column 존재 확인) 후 실행
- 데이터 migration(계정 plan_type 정규화, dashboard_settings singleton seed) 포함

### 3) Resilience Strategy

- unknown/non-contiguous legacy migration rows는 경고 로그로 노출
- stamp 가능한 prefix만 적용하고, 나머지는 idempotent upgrade로 수렴
- fail-fast 설정(`database_migrations_fail_fast`)은 기존 정책 유지
- SQLite pre-migration 백업은 회전 보관(`database_sqlite_pre_migrate_backup_max_files`)
- Docker 이미지는 서버 실행 전 `python -m app.db.migrate upgrade`를 선실행

### 4) Backward Compatibility

- 사용자 관점에서 "앱 실행 시 자동 마이그레이션" 동작은 유지
- 내부 구현만 표준 Alembic 기반으로 교체
- legacy table(`schema_migrations`)는 읽기 전용 bootstrap 정보로만 사용

### 5) Automation Guardrails

- CI에 migration drift 검사(job)를 추가해 ORM 메타데이터와 DB 스키마 차이가 있으면 실패
- drift 검사는 빈 SQLite DB에 `upgrade head` 후 `check` 명령으로 수행
