## Why

API-key quota reservation is committed before upstream rate-limit response
headers are calculated. If that calculation fails, four subscription-backed
request paths currently propagate the error before any downstream component
owns cleanup, leaving quota reserved until stale recovery runs.

The bridge-CI investigation also directly reproduced two lifecycle defects:
competing cleanup paths could close one HTTP bridge session twice, and failure
or cancellation after registry removal could leave no cleanup fallback. These
defects are included without claiming they caused the unreproduced CI-only 409.

## What Changes

- Retain reservation cleanup ownership while rate-limit headers are prepared
  for streaming Responses, collected Responses, Responses compaction, and
  audio transcription requests.
- Release an owned reservation exactly once when header preparation fails,
  then preserve the original failure instead of starting upstream work.
- Add one parameterized, route-level failure-injection regression that proves
  all four request shapes restore quota and perform exactly one release.
- Preserve successful header construction, downstream settlement ownership,
  and borrowed reservation behavior.
- Give HTTP bridge session cleanup one exact-once close owner across failed
  registration, scheduled cleanup, reader retirement, local terminal reset,
  and process shutdown.
- Keep detached bridge cleanup tracked and bounded through pending-cleanup
  failure or caller cancellation, and process every shutdown-owned session
  before propagating cancellation.
- Add deterministic lifecycle regressions for competing close paths and
  interrupted terminal-reset and shutdown cleanup.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: Extend the early-exit reservation cleanup contract to failures
  while rate-limit response headers are calculated after admission and before
  upstream ownership begins.
- `responses-api-compat`: Require exact-once HTTP bridge close ownership and
  cancellation-safe cleanup after a session leaves the local registry.

## Impact

- Backend: `app/modules/proxy/api.py` and
  `app/modules/proxy/_service/http_bridge/{account_sessions,helpers,mixin,request_submit,streaming}.py`
- Tests: `tests/integration/test_api_keys_api.py`,
  `tests/unit/test_proxy_api_responses_contract.py`, and
  `tests/unit/test_proxy_http_bridge.py`
- Build/CI: `Makefile` includes the route regression in the required
  PostgreSQL pytest target
- Contract: API-key quota cleanup on internal response-header failure and HTTP
  bridge exact-once/cancellation-safe session cleanup
- No API schema, database migration, dependency, setting, dashboard, or
  successful-response change
