## Why

`request_logs.service_tier` intentionally prefers the tier echoed by upstream, which means a successful log row alone cannot prove what `service_tier` the client actually requested. The existing request payload log is too sensitive for production use, while the request shape log only shows field presence, not the requested tier value.

## What Changes

- Add an opt-in proxy diagnostic log that records only `request_id`, request `kind`, `requested_service_tier`, and upstream `actual_service_tier`.
- Emit the trace for both streaming and compact Responses paths without logging prompt or payload content.
- Add regression tests and record the contract in OpenSpec.

## Impact

- Code: `app/core/config/settings.py`, `app/modules/proxy/service.py`
- Tests: `tests/unit/test_proxy_utils.py`
- Specs: `openspec/specs/responses-api-compat/spec.md`
