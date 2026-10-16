## 1. Fix

- [x] 1.1 Expire elapsed primary, secondary and monthly rows in `_build_aggregate_credit_limits`

## 2. Spec + tests

- [x] 2.1 MODIFIED `api-keys` `/v1/usage` requirement + elapsed-sample scenario
- [x] 2.2 Integration test: a silent account's elapsed 5h, 7d and monthly samples do not pin the aggregate reset or usage
- [x] 2.3 `pytest tests/integration/test_v1_usage.py` green
