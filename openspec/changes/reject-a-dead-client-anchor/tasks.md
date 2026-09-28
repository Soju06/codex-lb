## 1. Implementation

- [x] 1.1 `retire_dead_client_anchor(exc)` in `_stream_via_http_bridge_impl`: one attempt per
  request (shared with `retire_unavailable_continuity_owner`), gated on an owner-unavailable
  failure, a client-supplied `previous_response_id`, no file pin, and a durable lookup naming the
  account being retired. Retires through the same horizon rule, then returns
  `404 bridge_previous_response_not_found`.
- [x] 1.2 Raise it from the connect-failure branch **without** the pre-submit marker.

## 2. Regression coverage

- [x] 2.1 `tests/integration/test_http_responses_bridge.py`: a client-anchored resume on a paused
  owner is rejected 404 `bridge_previous_response_not_found` with an actionable message, exactly
  one `dead_anchor_owner_retired` event, and the following anchor-free resend is served 200 on a
  healthy account. Verified load-bearing by disabling the call and watching it fail.

- [x] 2.2 `tests/unit/test_durable_bridge_owner_retirement.py`: a racing duplicate is told the
  owner is retired; a recovered owner and a missing session are not. Updates the idempotence
  test from the previous change, which encoded the old "did I retire it" contract.

## 3. Validation

- [ ] 3.1 `make lint`, `make typecheck`.
- [ ] 3.2 `make test-unit`, `make test-integration-bridge`, `make test-integration-core-1..3`,
  `tests/e2e`.
- [ ] 3.3 `openspec validate reject-a-dead-client-anchor --strict`, `openspec validate --specs`.
- [ ] 3.4 `codex review --base origin/main`.
