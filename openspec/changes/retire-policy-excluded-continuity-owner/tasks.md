## 1. Implementation

- [x] 1.1 `_http_bridge_is_continuity_owner_policy_conflict(exc)` in the HTTP bridge helpers,
  matching the `continuity_owner_policy_conflict` error code.
- [x] 1.2 `retire_unavailable_continuity_owner(exc)` accepts that failure alongside
  `previous_response_owner_unavailable`; the remaining gates and the repository predicate are
  unchanged.

## 2. Regression coverage

- [x] 2.1 Integration: a rate-limited owner excluded by policy, with a reset horizon beyond the
  request budget, is retired and the turn is served on a healthy account in the same request.
  Verified to fail with 503 `continuity_owner_policy_conflict` without the change.
- [x] 2.2 Integration: a policy conflict on an active owner is not retired and still returns 503
  `continuity_owner_policy_conflict`.

## 3. Validation

- [x] 3.1 `uv run ruff check` and `uv run ruff format --check` on the touched files.
- [x] 3.2 `uv run pytest tests/integration/test_http_responses_bridge.py`
