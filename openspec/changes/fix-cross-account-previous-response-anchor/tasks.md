# Tasks

- [x] Add `last_completed_response_account_id` to `_HTTPBridgeSession`
- [x] Record the serving account on the real `response.completed` setter
      (`upstream_events.py`)
- [x] Record the durable owner account on the durable-restore setter
      (`streaming.py`)
- [x] Gate session-level compact-anchor injection on
      `last_completed_response_account_id == session.account.id`
- [x] Regression tests: anchor injected when same-account; anchor skipped and
      full history resent when cross-account failover
- [x] Verify full `test_proxy_http_bridge` + broader proxy suites green
- [ ] Follow-up (separate change): guard the durable direct-anchor injection that
      runs before account binding
- [ ] Follow-up (separate change): proactive `response.created` watchdog that
      replays stored full-history payload on stall
- [ ] Follow-up (separate change): audit the WebSocket-transport anchor path for
      the same cross-account exposure
