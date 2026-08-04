# Tasks

- [x] Investigate whether `recover-fresh-hard-bridge-timeouts` (#1394) already
      covers this change's original owner-side stuck-gate failover — confirm
      it does (its "Fresh hard bridge requests may recover across accounts"
      requirement), and re-scope this change to what's still open instead of
      duplicating that mechanism.
- [x] Drop `request_state.replay_count == 0` from
      `_http_bridge_can_replace_retired_gate_session`'s guard.
- [x] When that predicate accepts a waiter with no previous-response account
      pin, add the retired session's account to
      `request_state.excluded_account_ids` before building the replacement
      session.
- [x] Update `test_http_bridge_retired_gate_replacement_requires_unsubmitted_waiter`
      to drop its now-stale `replay_count` case, and add
      `test_http_bridge_retired_gate_replacement_ignores_replay_count`
      asserting a waiter with `replay_count=1` is still accepted.
- [x] Add `test_stream_via_http_bridge_replaces_retired_hard_gate_excludes_stuck_account`
      covering the account-exclusion fix end to end.
- [x] Update `test_stream_via_http_bridge_projects_plaintext_durable_full_resend_when_owner_is_unavailable`'s
      `replace_retired_gate=True` assertion, which previously locked in the
      gap this change fixes (a second stuck account was not excluded from a
      third replacement attempt).
- [x] Run focused and full test suites, ruff check/format, `ty check`, and
      the proxy architecture-check script.
