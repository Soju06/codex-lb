## 1. Fail closed on unknown client anchors

- [x] 1.1 Add failing unit tests: a Codex-shaped two-item delta whose
      `previous_response_id` is unknown to the connection (no continuity, or a
      different last completed response) is not replay-safe, and request
      preparation keeps the anchor and stores no fresh body.
- [x] 1.2 Require the anchor to be the connection's last completed response and
      the input to restate its stored prefix before a client-supplied anchor is
      treated as a replay-safe full resend.
- [x] 1.3 Keep a known-anchor self-contained full resend replay-safe.
- [x] 1.4 Record whether a completed request's input covered its whole context,
      and require it before a stored prefix proves a full resend, so a
      restated delta of a client-anchored completion is not replayed.

## 2. Align existing tests with the rule

- [x] 2.1 `test_backend_responses_websocket_connect_failure_logs_client_supplied_stale_anchor_metadata`:
      the anchor was never completed on that connection, so no fresh replay is
      available and the diagnostics report `fresh_replay_available=false`.
- [x] 2.2 `test_websocket_lite_fresh_replay_strips_trusted_marker`: the
      user-only delta after an accepted prewarm, and the Lite body anchored on
      an unknown response, are not replayed anchorless; the marker and
      reasoning-context assertions on the upstream payload stay.
- [x] 2.3 `test_backend_responses_websocket_lite_fresh_replay_drops_marker_after_previous_response_miss`
      replayed a restated delta of a client-anchored completion without the
      first turn's tool bundle. Renamed to
      `test_backend_responses_websocket_lite_restated_delta_is_not_replayed_after_previous_response_miss`;
      it now expects `previous_response_not_found` and no anchorless upstream body.

## 3. Verify

- [x] 3.1 `uv run pytest tests/unit/test_proxy_utils.py -q`
- [x] 3.2 `uv run pytest tests/integration/test_proxy_websocket_responses.py tests/integration/test_http_responses_bridge.py tests/integration/test_proxy_responses.py -q`
- [x] 3.3 `openspec validate --specs`
