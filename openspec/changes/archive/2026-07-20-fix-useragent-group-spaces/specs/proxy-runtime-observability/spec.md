## MODIFIED Requirements

### Requirement: Request logs persist prompt-client user-agent metadata

The proxy MUST persist prompt-client user-agent metadata on `request_logs` for both HTTP and WebSocket Responses traffic. Each persisted row MUST store the stripped nonblank inbound `User-Agent` header value and a derived `useragent_group` value formed from the trimmed user-agent content before the first `/`. When the inbound header is missing or blank after trimming, both persisted values MUST be `null`. When no `/` is present, `useragent_group` MUST be the entire trimmed user-agent value.

#### Scenario: HTTP request log stores a normalized user-agent group

- **WHEN** an HTTP or HTTP/SSE proxy request includes `User-Agent: opencode/1.15.13 ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14`
- **THEN** the persisted `request_logs` row stores `useragent = "opencode/1.15.13 ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14"`
- **AND** the persisted row stores `useragent_group = "opencode"`

#### Scenario: WebSocket request log stores a normalized user-agent group

- **WHEN** a proxied WebSocket Responses session is opened with `User-Agent: opencode/1.15.13 ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14`
- **THEN** the persisted `request_logs` row for that request stores the stripped nonblank header in `useragent`
- **AND** the persisted row stores `useragent_group = "opencode"`

#### Scenario: Codex Desktop request log preserves the product name in its group

- **WHEN** a proxied request includes `User-Agent: Codex Desktop/0.142.4 (Mac OS 26.5.2; arm64) unknown (Codex Desktop; 26.623.70822)`
- **THEN** the persisted `request_logs` row stores `useragent = "Codex Desktop/0.142.4 (Mac OS 26.5.2; arm64) unknown (Codex Desktop; 26.623.70822)"`
- **AND** the persisted row stores `useragent_group = "Codex Desktop"`

#### Scenario: Leading and trailing whitespace is excluded from the group

- **WHEN** a proxied HTTP or WebSocket request includes a nonblank user-agent with surrounding whitespace and a `/`
- **THEN** `useragent` stores the trimmed nonblank user-agent
- **AND** `useragent_group` contains the trimmed content before the first `/`
- **AND** `useragent_group` does not contain surrounding whitespace

#### Scenario: User-agent without a slash uses its trimmed value

- **WHEN** a proxied HTTP or WebSocket request includes a nonblank user-agent without a `/`
- **THEN** `useragent_group` equals the entire trimmed user-agent value

#### Scenario: Missing or blank user-agent remains null

- **WHEN** a proxied HTTP or WebSocket request omits the `User-Agent` header or sends only blank whitespace
- **THEN** the persisted `request_logs` row stores `useragent = null`
- **AND** the persisted `request_logs` row stores `useragent_group = null`
