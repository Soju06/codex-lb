## Why

`codex-lb`는 현재 자체 `schema_migrations` 러너와 런타임 `create_all` 조합으로 스키마를 관리한다. 이 방식은 환경별 상태 드리프트에 취약하고, 표준 도구와의 연계(검증, 운영 자동화, 롤백 전략)에도 불리하다. one-click 오픈소스 특성상 다양한 사용자 DB 상태를 자동으로 수렴시키는 견고한 표준 경로가 필요하다.

## What Changes

- 커스텀 마이그레이션 러너를 제거하고 Alembic을 단일 SSOT로 채택
- 앱 부팅 시 Alembic `upgrade head`를 실행하는 one-click 경로 유지
- 기존 `schema_migrations` 기록을 `alembic_version`으로 자동 bootstrap(stamp) 후 업그레이드
- SQLite/PostgreSQL 모두에서 동일한 revision 체인을 사용
- 운영/수동 실행용 마이그레이션 CLI(`python -m app.db.migrate`, `codex-lb-db`) 제공
- SQLite 환경에서 migration 적용 전 자동 백업 생성(회전 보관)
- CI에서 migration drift를 자동 검사해 모델/리비전 불일치를 차단
- Docker 런타임에서 앱 기동 전 migration 선적용

## Impact

- 기존 내부 마이그레이션 모듈(`app/db/migrations`) 제거
- DB 초기화 경로가 `create_all + custom`에서 `alembic upgrade`로 변경
- 테스트 기준이 Alembic 경로로 전환
- PostgreSQL migration parity를 위해 driver 의존성 추가
