## ADDED Requirements

### Requirement: API-key quota privacy covers native Codex stream events

When `hide_upstream_quota_from_api_keys` is enabled, API-key-authenticated
Responses SSE and WebSocket streams MUST omit upstream `codex.rate_limits`
events, including model-specific families. Owner requests without API-key
authentication MUST continue to receive pooled quota events when available.
The API key's own self-usage endpoint and limits MUST remain unchanged.

#### Scenario: Hidden headers cannot be bypassed through streaming metadata

- **GIVEN** upstream quotas are hidden from API keys
- **WHEN** upstream emits a quota event on a native Codex SSE or WebSocket stream
- **THEN** the API-key client receives neither pooled quota headers nor upstream quota events
- **AND** normal response events remain available
