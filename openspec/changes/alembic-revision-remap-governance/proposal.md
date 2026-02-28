## Why

기존 Alembic 리비전이 `000~013` 순번 기반이라 병렬 브랜치에서 동일 번호/선형 의존 충돌이 자주 발생한다. 운영 DB에 이미 배포된 구 리비전 ID를 안전하게 흡수하면서 충돌 비용을 줄이는 표준 규약과 자동 컷오버가 필요하다.

## What Changes

- Alembic 리비전 ID를 `YYYYMMDD_HHMMSS_slug` 형식으로 표준화한다.
- 기존 `000~013` 리비전을 타임스탬프 기반 ID로 일괄 재작성한다.
- 앱 startup migration 경로에서 구 Alembic 리비전 ID를 신 ID로 자동 remap한다.
- `codex-lb-db check`에 head/네이밍 정책 검증을 추가하고 drift 검증과 통합한다.
- CI migration-check를 정책 통합 검증 경로로 고정한다.
- project-conventions와 OpenSpec SSOT에 마이그레이션 거버넌스 원칙을 명시한다.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `database-migrations`: 리비전 ID 규약, 자동 remap 컷오버, CI 정책 검증 요구사항을 추가한다.

## Impact

- `app/db/alembic/versions/*` 파일명 및 revision/down_revision 식별자 변경
- `app/db/migrate.py`의 upgrade/check 동작 확장(자동 remap, 정책 검증)
- 신규 매핑/변환 유틸 추가(`app/db/alembic/revision_ids.py`, `scripts/rewrite_alembic_revisions.py`)
- 테스트/CI 기대값 갱신
- OpenSpec `database-migrations` spec/context 및 project-conventions 갱신
