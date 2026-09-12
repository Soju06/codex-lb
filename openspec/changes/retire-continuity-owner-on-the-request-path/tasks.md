## 1. Implementation

- [x] 1.1 `DurableBridgeRepository.retire_continuity_owner_if_unavailable(session_id, *,
  expected_account_id, recovery_deadline_epoch)` mirroring
  `abandon_legacy_session_header_owner_if_unavailable`: lock the account row with
  `SELECT ... FOR UPDATE`, refuse when the status has recovered or the reset horizon falls before
  the deadline, then CAS on row id + expected owner + both markers NULL + status still
  unavailable. Writes `continuity_abandonment_scope="request_path"` only.
- [x] 1.2 `DurableBridgeSessionCoordinator.retire_continuity_owner_if_unavailable` wrapper.
- [x] 1.3 `retire_unavailable_continuity_owner(exc)` in `_stream_via_http_bridge_impl`: one
  attempt per request, gated on an owner-unavailable failure, a proxy-injected anchor
  (`payload.previous_response_id is None`), no file pin, and a durable lookup naming the account
  being retired. On success re-prepare the turn from
  `_http_bridge_payload_without_previous_response_id(untrimmed_effective_payload)`, drop the
  owner and the durable lookup, log `owner_retired_on_request`, and `continue` the connect loop.
- [x] 1.4 Project the monotonic request deadline onto the wall clock `reset_at` uses, rather than
  comparing the two clock domains.

## 2. Regression coverage

- [x] 2.1 `tests/unit/test_durable_bridge_owner_retirement.py`: no horizon retires; a horizon
  inside the budget waits; a horizon after it retires; a healthy owner is never retired; an owner
  mismatch is refused; idempotent; cleared by a fresh claim.
- [x] 2.2 `tests/integration/test_http_responses_bridge.py`: a hard `thread_header` resume whose
  owner is paused is served **200** on a healthy account inside the same request, with exactly one
  `owner_retired_on_request` event, no sweep and no grace; the durable row ends owned by the
  replacement with no marker left.
- [x] 2.3 Strengthen the sweep's end-to-end test from the previous change to use the same hard
  `thread_header` key. It previously ran over a soft prompt-cache key, which falls back to a
  healthy account on its own and so proved nothing; verified by removing the
  `continuity_abandoned` exemption and watching it fail.

- [x] 2.4 `retire_stale_unavailable_bridge_owners` phase 1 promotes a scope-only marker to its
  own global form once the row is stale, mirroring the sticky sweep's promotion. Without it a
  request-path retirement satisfies neither phase and leaks. Promotion deliberately does not
  require the owner to still be unavailable. Covered by
  `test_an_unclaimed_request_path_marker_is_collected` and
  `test_promotion_does_not_need_the_owner_to_be_unavailable`.

## 3. Validation

- [ ] 3.1 `make lint`, `make typecheck`.
- [ ] 3.2 `make test-unit`, `make test-integration-bridge`, `make test-integration-core-1..3`,
  `tests/e2e`.
- [ ] 3.3 `openspec validate retire-continuity-owner-on-the-request-path --strict`,
  `openspec validate --specs`.
- [ ] 3.4 `codex review --base origin/main`.
