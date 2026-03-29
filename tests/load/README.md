# Load testing (`k6`)

This folder contains local/manual load-testing scripts for codex-lb.

## Prerequisites

1. Install k6: https://grafana.com/docs/k6/latest/set-up/install-k6/
2. Run codex-lb locally and expose it on `http://localhost:2455` (or pass a custom `BASE_URL`).

## Start mock upstream server

Run the mock upstream in a separate terminal:

```bash
python3 tests/load/helpers/mock-upstream.py
```

The mock server listens on `http://localhost:8080` and provides:

- `POST /backend-api/conversation` (SSE-style streaming)
- `GET /public-api/me`

## Run the baseline test

```bash
k6 run tests/load/baseline.js
```

With custom target URL:

```bash
BASE_URL=http://localhost:2455 k6 run tests/load/baseline.js
```

Baseline target profile:

- Ramp to 100 VUs
- Total duration: 5 minutes
- Coverage: `/health`, `/health/ready`, `/api/accounts`
- Thresholds: `error_rate < 1%`, `p(95) http_req_duration < 2000ms`

## Interpret results

Focus on:

- `error_rate` threshold pass/fail
- `http_req_duration` p(95)
- HTTP status distributions for health/readiness/accounts routes

Suggested baseline acceptance:

- Error rate under 1%
- p95 latency under 2s
- No sustained readiness failures

## Run other scenarios

Stress (ramp to 500 VUs):

```bash
k6 run tests/load/stress.js
```

Soak (50 VUs for 30 minutes):

```bash
k6 run tests/load/soak.js
```

Spike (sudden jump to high concurrency):

```bash
k6 run tests/load/spike.js
```

These scripts are intended for manual execution and should not be added to CI pipelines.
