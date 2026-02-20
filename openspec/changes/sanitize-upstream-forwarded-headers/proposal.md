## Why

홈서버 환경(`docker -> nginx reverse proxy -> cloudflare -> user`)에서 codex-lb를 원격 사용하면 upstream(ChatGPT)에서 Cloudflare 차단 페이지가 반환되는 문제가 발생했다. 원인 분석 결과, codex-lb가 inbound 헤더를 그대로 upstream으로 전달하면서 프록시 체인에서 추가된 `Forwarded`, `X-Forwarded-*`, `CF-*` 계열 헤더까지 전파되고 있었다.

로컬 직결 환경에서는 재현되지 않고, reverse proxy + Cloudflare 환경에서만 재현된 점과 헤더 제거(`clean`) 경로에서 즉시 정상화된 점을 기준으로, upstream 호출 전에 프록시 식별 헤더를 명시적으로 제거해야 한다.

## What Changes

- Upstream 호출 전 inbound 헤더 필터링 규칙 강화
  - 기존 제거 대상(`Authorization`, `chatgpt-account-id`, `Content-Length`, `Host`) 유지
  - `Forwarded`, `X-Forwarded-*`, `X-Real-IP`, `True-Client-IP`, `CF-*` 제거 추가
- 필터링 회귀 테스트 추가
  - unit: 헤더 필터 함수에서 프록시 식별 헤더가 제거되는지 검증
  - integration: `/backend-api/codex/responses` 경로에서 upstream 호출 mock에 전달되는 헤더 검증

## Capabilities

### Modified Capabilities

- `responses-api-compat`: upstream 전달 시 네트워크/프록시 식별 헤더를 제거하는 계약 추가

## Impact

- **코드**: `app/core/clients/proxy.py`
- **테스트**: `tests/unit/test_proxy_utils.py`, `tests/integration/test_proxy_api_extended.py`
- **API 계약**: 응답 스키마/필드 변화 없음, upstream 전달 헤더 정책만 강화
