## MODIFIED Requirements

### Requirement: Request logs persist prompt-client user-agent metadata
The proxy MUST persist prompt-client user-agent metadata on `request_logs` for both HTTP and WebSocket Responses traffic. Each persisted row MUST store the full inbound `User-Agent` header value when present and a derived `useragent_group` value. When the inbound header contains `/`, `useragent_group` MUST be the complete sequence of characters before its first `/`; when it contains no `/`, the existing group extraction behavior MUST remain unchanged. When the inbound header is missing or blank after trimming, both persisted values MUST be `null`.

#### Scenario: Historical request-log user-agent families are backfilled without normalization
- **WHEN** the user-agent family migration processes historical `request_logs` rows
- **THEN** rows whose `useragent` is non-null and contains `/` MUST have `useragent_group` set to the exact unprocessed full prefix before the first `/`
- **AND** rows whose `useragent` is `null` or contains no `/` MUST remain unchanged

#### Scenario: HTTP request log stores user-agent metadata
- **WHEN** an HTTP or HTTP/SSE proxy request includes `User-Agent: opencode/1.15.13 ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14`
- **THEN** the persisted `request_logs` row stores `useragent = "opencode/1.15.13 ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14"`
- **AND** the persisted row stores `useragent_group = "opencode"`

#### Scenario: Multi-word product family retains its full prefix
- **WHEN** an HTTP or HTTP/SSE proxy request includes `User-Agent: Codex Desktop/0.142.4`
- **THEN** the persisted `request_logs` row stores `useragent_group = "Codex Desktop"`

#### Scenario: WebSocket request log stores user-agent metadata
- **WHEN** a proxied WebSocket Responses session is opened with `User-Agent: opencode/1.15.13 ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14`
- **THEN** the persisted `request_logs` row for that request stores the full header in `useragent`
- **AND** the persisted row stores `useragent_group = "opencode"`

#### Scenario: Missing or blank user-agent remains null
- **WHEN** a proxied HTTP or WebSocket request omits the `User-Agent` header or sends only blank whitespace
- **THEN** the persisted `request_logs` row stores `useragent = null`
- **AND** the persisted row stores `useragent_group = null`
