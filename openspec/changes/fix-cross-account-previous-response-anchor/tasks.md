# Tasks

- [x] Add `last_completed_response_account_id` to `_HTTPBridgeSession`
- [x] Record the serving account on the real `response.completed` setter
      (`upstream_events.py`)
- [x] Record the durable owner account on the durable-restore setters
      (`streaming.py`), and clear it with the anchor on an account-changing
      reconnect (`mixin.py`)
- [x] Gate session-level compact-anchor injection on
      `last_completed_response_account_id == session.account.id`
- [x] Gate the owner-forward recovery anchor injection on
      `durable_lookup.account_id == session.account.id` — the recovery rebind is
      explicitly allowed to land on a different account
- [x] Log `cross_account_anchor_declined` at both sites so the wedge family stays
      observable from bridge events
- [x] Regression tests: anchor injected when same-account; anchor skipped and
      full history resent when cross-account, at both injection sites
- [x] Bridge-level regression: real bridge session, real response-create gate,
      upstream that models the account scope of `previous_response_id` (silence
      for a foreign anchor) — the turn settles instead of wedging
- [x] Verify full `test_proxy_http_bridge` + bridge integration suites green
- [ ] Follow-up (separate change): proactive `response.created` watchdog that
      replays stored full-history payload on stall
- [ ] Follow-up (separate change): audit the WebSocket-transport anchor path
      (`websocket_session_anchor_injected`) for the same cross-account exposure
