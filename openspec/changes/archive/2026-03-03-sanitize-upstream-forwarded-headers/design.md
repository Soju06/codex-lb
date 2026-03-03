## Context

현재 proxy 경로는 `request.headers`를 서비스 계층으로 전달하고, `filter_inbound_headers()`에서 일부 헤더만 제거한 뒤 upstream request 헤더의 base로 사용한다. 제거 대상이 제한적이라 reverse proxy/Cloudflare가 추가한 프록시 식별 헤더가 upstream(ChatGPT)까지 전달된다.

## Goal / Non-Goal

**Goal**
- upstream 요청에서 프록시 체인 식별 헤더를 일관되게 제거한다.
- stream/compact 경로 모두 동일 정책을 적용한다.
- 동작을 테스트로 고정해 회귀를 방지한다.

**Non-Goal**
- API 응답 포맷 변경
- nginx/cloudflare 설정 자동 수정
- upstream 차단 정책 자체 우회 로직 추가

## Decision

### 1) 중앙 필터 규칙 확장

`app/core/clients/proxy.py`에서 inbound 헤더 필터 함수를 확장한다.

- 제거할 단일 헤더:
  - `forwarded`
  - `x-real-ip`
  - `true-client-ip`
  - 기존 제거 대상(`authorization`, `chatgpt-account-id`, `content-length`, `host`)
- 제거할 접두사:
  - `x-forwarded-`
  - `cf-`

이 정책은 stream/compact 공통 경로에서 동일하게 적용된다.

### 2) 회귀 테스트

- unit: 필터 함수가 proxy identity 헤더를 제거하고 일반 헤더는 유지하는지 검증
- integration: `/backend-api/codex/responses` 호출 시 monkeypatch된 upstream 함수에 전달된 헤더에서 proxy identity 헤더가 제외되는지 검증

## Trade-offs

- `cf-*` 전부 제거는 보수적 정책이다. 일부 비식별 헤더도 함께 제거될 수 있지만 upstream 호환성/안정성 측면에서 안전한 기본값이다.
- 네트워크 환경이 다양해도 애플리케이션 레벨에서 동일한 최소 헤더 정책을 강제할 수 있다.
