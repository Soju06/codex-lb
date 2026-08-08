## Why

The HTTP bridge's previous-response reuse path can return an already-live session after first publishing a new, unresolved session-creation future for that same anchor. That orphaned future permanently marks the bridge as restart-blocking and makes later requests fail with a continuity 502, so the create chain must not run when reuse has already selected a session.

## What Changes

- Prevent the generic HTTP bridge session-creation arm from publishing an inflight future when the previous-response path has selected an existing session for return.
- Preserve the existing done-future janitor contract and all other session-creation and handoff arms.
- Keep regression coverage for both registry cleanup and a successful second request on the same previous-response anchor.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: HTTP bridge previous-response reuse must not leave an unresolved session-creation future registered for the reused anchor.

## Impact

- Affected code: `app/modules/proxy/_service/http_bridge/mixin.py` session lookup/create chain.
- Affected tests: focused HTTP bridge bughunt regression and existing unit/integration bridge suites.
- No API schema, persistence, or janitor behavior changes.
