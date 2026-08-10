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
- [x] Fix a correctness gap found in review (08-06): the exclusion branch
      also fired for a waiter whose replacement is already required to land
      on a specific account (a resolved previous-response owner, or a
      file-pinned account) — excluding that required account made its own
      replacement impossible and poisoned later recovery calls on the
      request, since `excluded_account_ids` persists on `request_state`.
      Only exclude when the replacement is genuinely unpinned
      (`replacement_preferred_account_id is None`).
- [x] Add `test_stream_via_http_bridge_replaces_retired_hard_gate_keeps_pinned_account_unexcluded`
      covering the pinned-waiter case.
- [x] Reword the `replay_count` relaxation's justification (in code comments
      and this spec) away from "reflects client-side reconnect attempts" —
      it's also incremented at proxy-side resubmission points, so that
      framing is imprecise. The relaxation is justified by the predicate's
      other definitively-unsubmitted markers, not by what increments the
      counter.
- [x] Re-run focused and full test suites, ruff check/format, `ty check`,
      and the architecture-check script after the fix.
