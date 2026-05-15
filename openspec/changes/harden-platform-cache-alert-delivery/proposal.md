## Why

Platform cache-miss alerting currently depends on a configurable proxy URL and sends only the Platform API-key suffix. Operations now have a single alert ingress and need enough client context to triage which user runtime is producing uncached Platform traffic.

## What Changes

- Send Platform cache-miss alerts to the fixed `https://codex-lb-alert.cinamon.io` alert proxy.
- Include the requesting client's version in the alert payload alongside the Platform API-key suffix.
- Suppress all alert delivery attempts for one hour after an alert delivery failure so a down alert proxy does not create repeated outbound attempts.

## Impact

- Code: `app/modules/proxy/platform_cache_alerts.py`, `app/modules/proxy/service.py`, `app/modules/proxy/api.py`, settings cleanup.
- Tests: update cache alert unit and Platform proxy integration coverage.
