## 1. OpenSpec

- [x] 1.1 Define the account inflight lease metric contract.

## 2. Backend

- [x] 2.1 Add the Prometheus account inflight lease gauge.
- [x] 2.2 Update the gauge from account lease acquire, release, and stale reclaim paths.

## 3. Validation

- [x] 3.1 Add metrics registry coverage.
- [x] 3.2 Add load-balancer lease lifecycle coverage.
- [x] 3.3 Run OpenSpec validation when the CLI is available locally (`openspec` and `uv run openspec` are unavailable in this environment).
- [x] 3.4 Run targeted tests, ruff, format check, and diff check.
