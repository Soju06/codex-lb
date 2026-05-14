## ADDED Requirements

### Requirement: Repeated Platform prompt-cache misses emit a redacted alert
When Platform cache-miss alerting is configured, the system SHALL observe successful OpenAI Platform Responses requests with usage data and maintain a rolling window of the latest 7 cache observations per Platform API-key suffix. A request SHALL count as uncached when `input_tokens > 0` and `cached_input_tokens` is absent or zero. When at least 4 observations in that 7-request window are uncached, the system SHALL POST only the Platform API key's last 4 characters to the configured alert proxy and SHALL NOT include the full API key, request body, prompt text, or token counts.

#### Scenario: Four misses in seven requests trigger an alert
- **WHEN** Platform cache-miss alerting is configured
- **AND** the latest 7 Platform Responses observations for the same Platform API-key suffix contain at least 4 uncached requests
- **THEN** the system POSTs the API-key suffix to the configured alert proxy

#### Scenario: Fewer than four misses do not trigger
- **WHEN** Platform cache-miss alerting is configured
- **AND** the latest 7 Platform Responses observations for the same Platform API-key suffix contain fewer than 4 uncached requests
- **THEN** the system does not send an alert

#### Scenario: Alert payload is redacted
- **WHEN** a Platform cache-miss alert is sent
- **THEN** the request body contains only the Platform API key's last 4 characters
- **AND** it does not contain the full Platform API key, request payload, prompt text, or token counts

#### Scenario: Alert delivery is best effort
- **WHEN** the configured alert proxy is unavailable, times out, or returns an error
- **THEN** the proxied API request result is preserved
- **AND** the alert failure is logged without surfacing the alert failure to the API client

#### Scenario: Cooldown suppresses duplicate alerts
- **WHEN** a Platform API-key suffix has already emitted an alert inside the configured cooldown window
- **AND** subsequent observations still satisfy the 4-of-7 uncached threshold
- **THEN** the system does not send another alert for that suffix until the cooldown window expires
