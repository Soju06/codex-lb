## Context

현재 프로젝트는 Alembic을 단일 마이그레이션 SSOT로 사용하지만, revision ID가 `000~013` 순번 기반이라 병렬 개발 시 충돌이 빈번하다. 이미 운영 DB에 기록된 기존 `alembic_version` 값은 구 ID 체계라서 파일명/리비전 전면 전환 시 런타임 컷오버 전략이 필요하다.

## Goals / Non-Goals

**Goals:**
- 리비전 ID를 충돌 저감형 타임스탬프 포맷으로 전환한다.
- 기존 운영 DB를 수동 SQL 없이 startup 자동 remap으로 수렴시킨다.
- CI에서 단일 head + 리비전 네이밍 + drift를 한 번에 검증한다.
- 규약을 OpenSpec/skill에 명시해 신규 마이그레이션 작성 기준을 고정한다.

**Non-Goals:**
- 기존 마이그레이션 SQL 의미/스키마 동작 변경
- 월/분 단위 등 다른 리비전 포맷 지원
- 다중 head를 영구 허용하는 운영 정책

## Decisions

1. **리비전 ID 포맷 고정**
   - `YYYYMMDD_HHMMSS_[a-z0-9_]+`만 허용한다.
   - 이유: 사람 가독성과 충돌 저감 사이 균형이 좋고, CI에서 정규식으로 강제 가능하다.

2. **구 리비전 전면 리네임 + runtime remap 병행**
   - 기존 14개 리비전을 새 ID로 재작성하고 startup `upgrade` 직전에 `alembic_version` 값을 자동 remap한다.
   - 이유: 코드/파일 구조 일관성을 유지하면서 기존 운영 DB와 호환할 수 있다.

3. **정책 검증 통합 (`codex-lb-db check`)**
   - 단일 head, 리비전 ID 형식, 파일명-리비전 일치, ORM drift를 하나의 검사 명령으로 묶는다.
   - 이유: 머지 게이트를 단순화하고 누락 가능한 수동 점검 단계를 제거한다.

4. **불변 이력 규칙 명시**
   - 머지된 migration 수정 금지, 보정 migration 추가 원칙을 project-conventions와 OpenSpec에 명시한다.
   - 이유: 팀 규모가 커질수록 충돌 해결을 재작성보다 merge revision/보정 migration으로 수렴해야 안전하다.

## Risks / Trade-offs

- [운영 중 구 리비전 + 신 코드 혼재] -> startup remap을 기본 활성화하고 미지원 ID는 fail-fast로 조기 감지
- [전면 리네임으로 테스트/참조 대량 수정] -> 단일 매핑 SSOT 모듈로 참조 경로를 통일
- [다중 head 발생 가능성] -> 개발 중 허용하되 CI에서 실패시켜 release 전 merge revision 강제
- [롤백 복잡도 증가] -> 컷오버를 one-way로 정의하고 DB 백업 복원 중심 런북 유지

## Migration Plan

1. 리비전 매핑 SSOT와 일회성 리라이트 스크립트 추가
2. `app/db/alembic/versions`를 새 ID로 일괄 재작성
3. runtime remap + 정책 체크 로직 추가
4. 테스트/CI/규약 문서 업데이트
5. 점검창구 배포에서 startup 로그로 remap 결과 확인

## Open Questions

- None. 본 변경은 결정 완료 상태로 구현한다.
