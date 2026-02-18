## Why

`/api/dashboard-auth/totp/setup/*`가 세션 쿠키의 `pw=true`만 검사하면, 과거에 발급된 쿠키를 비밀번호 제거 이후에도 재사용할 수 있다. 이 경우 비밀번호 인증이 비활성화된 상태에서도 TOTP 시크릿이 다시 설정되어 인증 상태 일관성이 깨질 수 있다.

## What Changes

- TOTP setup 시작/확정 요청에서 `pw=true` 쿠키뿐 아니라 현재 `password_hash` 존재 여부(비밀번호 모드 활성화)를 함께 검증
- 비밀번호가 제거된 상태(`password_hash = NULL`)에서 stale 세션 쿠키를 재주입해도 setup 요청을 401로 차단
- 회귀 방지를 위해 통합 테스트에 stale 쿠키 재사용 시나리오 추가

## Impact

- 대시보드 인증 경계가 현재 설정 상태와 세션 상태를 동시에 반영
- 비밀번호 제거 이후 남은 세션 토큰 재사용으로 인한 TOTP 재설정/잠금 위험 감소
