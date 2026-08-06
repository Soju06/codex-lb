## Why

API-key quota reservation is committed before upstream rate-limit response
headers are calculated. If that calculation fails, four subscription-backed
request paths currently propagate the error before any downstream component
owns cleanup, leaving quota reserved until stale recovery runs.

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

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: Extend the early-exit reservation cleanup contract to failures
  while rate-limit response headers are calculated after admission and before
  upstream ownership begins.

## Impact

- Backend: `app/modules/proxy/api.py`
- Tests: `tests/integration/test_api_keys_api.py`
- Contract: API-key quota cleanup on internal response-header failure only
- No API schema, database migration, dependency, setting, dashboard, or
  successful-response change
