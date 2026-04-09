# 부하 테스트 (`k6`)

이 디렉터리는 `codex-lb`를 로컬에서 수동으로 부하 테스트할 때 사용하는 스크립트를 담고 있습니다.

## 준비

1. `k6` 설치: <https://grafana.com/docs/k6/latest/set-up/install-k6/>
2. `codex-lb`를 로컬에서 실행하고 `http://localhost:2455` 로 접근 가능하게 준비

## 모의 업스트림 서버 실행

다른 터미널에서 아래 명령으로 mock upstream을 띄웁니다.

```bash
python3 tests/load/helpers/mock-upstream.py
```

mock 서버는 `http://localhost:8080` 에서 다음 엔드포인트를 제공합니다.

- `POST /backend-api/conversation`
- `GET /public-api/me`

## 기본 시나리오 실행

```bash
k6 run tests/load/baseline.js
```

대상 주소를 바꾸려면:

```bash
BASE_URL=http://localhost:2455 k6 run tests/load/baseline.js
```

기본 시나리오:

- 최대 100 VU까지 증가
- 총 5분 실행
- `/health`, `/health/ready`, `/api/accounts` 포함
- 기준: `error_rate < 1%`, `p(95) http_req_duration < 2000ms`

## 결과 확인

주로 보면 되는 항목:

- `error_rate`
- `http_req_duration` p95
- health / readiness / accounts 라우트의 상태 코드 분포

권장 기준:

- 에러율 1% 미만
- p95 지연 2초 미만
- readiness 실패가 지속적으로 발생하지 않을 것

## 다른 시나리오

스트레스 테스트:

```bash
k6 run tests/load/stress.js
```

소크 테스트:

```bash
k6 run tests/load/soak.js
```

스파이크 테스트:

```bash
k6 run tests/load/spike.js
```

이 스크립트들은 수동 실행용이며 CI에는 포함하지 않는 전제를 따릅니다.
