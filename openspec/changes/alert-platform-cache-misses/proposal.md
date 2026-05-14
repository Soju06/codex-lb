## Why

Platform fallback can preserve request availability while silently losing prompt-cache locality. Operators need an automatic signal when recent Platform requests show repeated cache misses so the affected key can be investigated before usage cost spikes.

## What Changes

- Track recent OpenAI Platform Responses usage per Platform API-key suffix.
- Treat a Platform request as uncached when it has input tokens but no cached input tokens.
- When at least 4 of the latest 7 Platform API requests for the same key suffix are uncached, notify the configured Slack cache alert proxy with only the Platform API key's last 4 characters.
- Keep alert delivery best-effort so upstream proxy responses are never failed by alert transport errors.
- Add tests for the 7-request rolling window, key-suffix redaction, cooldown behavior, and API-path integration.

## Capabilities

### New Capabilities

### Modified Capabilities

- `proxy-runtime-observability`: add repeated Platform prompt-cache miss alerting with redacted key identification.

## Impact

- Affected code: Platform Responses request handling in `app/modules/proxy/api.py` and `app/modules/proxy/service.py`.
- Affected runtime config: optional alert proxy URL and alert threshold/cooldown settings.
- Affected tests: proxy alert unit tests plus Platform proxy integration coverage.
- No database schema changes and no changes required in `slack-cache-alert-proxy`.
