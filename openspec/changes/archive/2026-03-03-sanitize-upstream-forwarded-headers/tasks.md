## 1. Header Sanitization

- [x] 1.1 `app/core/clients/proxy.py`에 inbound header drop 규칙 확장 (`Forwarded`, `X-Forwarded-*`, `X-Real-IP`, `True-Client-IP`, `CF-*`)
- [x] 1.2 stream/compact 공통 경로에서 기존 필터 함수만 사용해도 정책이 적용되는지 확인

## 2. Tests

- [x] 2.1 unit 테스트 추가: proxy identity 헤더 제거 + 일반 헤더 유지 검증
- [x] 2.2 integration 테스트 추가: `/backend-api/codex/responses`에서 upstream mock으로 전달된 헤더 검증

## 3. Spec Delta

- [x] 3.1 `openspec/changes/sanitize-upstream-forwarded-headers/specs/responses-api-compat/spec.md`에 requirements delta 추가
- [x] 3.2 `openspec validate --specs` 실행
