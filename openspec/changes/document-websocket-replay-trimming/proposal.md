## Why

Responses websocket continuity can receive client-side full replay payloads after the proxy has already preserved upstream continuity metadata. Without a documented contract, future compatibility work can accidentally forward anchored assistant/tool-call history back upstream or retry an oversized full replay as if it were a safe fresh request.

## What Changes

- Document that websocket full-replay anchoring trims previously completed upstream output items before forwarding the request.
- Document that transparent fresh replay after `previous_response_not_found` is allowed only when the original payload remains within the upstream request size budget.
- Add regression coverage for replay trimming, injected-anchor waits, duplicate tool-call suppression, and safe fresh replay.

## Impact

- Affected code: `app/modules/proxy/service.py`
- Affected tests: `tests/unit/test_proxy_utils.py`
- Affected API: websocket Responses proxying and HTTP bridge request preparation metadata.
