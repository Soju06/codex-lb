## MODIFIED Requirements

### Requirement: Realtime call creation binds the final account under an authenticated caller scope

The proxy SHALL require either a registered Proxy API Key or a verified, policy-authorized ChatGPT OAuth caller for `POST /backend-api/codex/realtime/calls`, independently of the ordinary proxy auth toggle. A bearer beginning with `sk-clb-` MUST use required Proxy Key validation and MUST NOT fall back to OAuth after denial. OAuth MUST require a verified imported caller Account, active policy, and non-empty currently active allowed-account set before selection.

After a successful upstream response with an exact supported bounded call id, the proxy MUST bind the final successful account immutably under caller-specific affinity material. Key callers MUST retain the previous digest input of raw `api_key.id`; OAuth callers MUST use `oauth:{caller_account_id}`. The reserved prefix, digest formula, persistence kind, expiry, cleanup, privacy, and created-but-unbindable failure contracts remain unchanged.

#### Scenario: Registered Key behavior remains compatible

- **GIVEN** a registered Proxy API Key and an existing or new Live call
- **WHEN** call creation and sideband resolve affinity
- **THEN** the digest is byte-for-byte identical to the pre-change `SHA256(api_key.id + NUL + call_id)` value
- **AND** assignments, limits, reservations, last-used updates, and API-key request-log attribution retain existing behavior

#### Scenario: Verified OAuth caller creates a policy-scoped call

- **GIVEN** a verified imported OAuth caller with an active policy and non-empty allowed set
- **WHEN** call creation succeeds on an allowed account
- **THEN** the call binds under the caller Account's OAuth affinity material
- **AND** no default or synthetic Proxy API Key is loaded or created

#### Scenario: OAuth policy is absent or inactive

- **WHEN** valid OAuth credentials have no active policy or no currently active allowed account
- **THEN** the route returns credential-safe `403 oauth_live_not_enabled` before account selection
- **AND** no ownership mapping or upstream call is created

### Requirement: Every sideband route revalidates the authenticated caller and exact owner

`WS /backend-api/codex/{call_id}`, `WS /v1/live/{call_id}`, and `WS /v1/realtime?call_id={call_id}` SHALL use the same caller resolver as call creation. Key callers MUST resolve ownership with the unchanged Key affinity input and current assignment checks. OAuth callers MUST resolve ownership with their internal caller Account id and MUST confirm the immutable owner remains in the caller's current active allowed set. Every route MUST use the same normalizer, exact-owner selection, fresh owner load, reattach lease, relay, and connector behavior. Missing, revoked, inactive, disallowed, or unavailable ownership fails closed without refresh, fallback, or owner disclosure.

#### Scenario: OAuth sideband joins its call

- **GIVEN** an OAuth caller created a bound call and its policy still allows the owner
- **WHEN** the first-party sideband reaches `/v1/realtime?intent=...&call_id=...`
- **THEN** it reloads and leases the exact bound owner
- **AND** forwards with the owner's current upstream identity

#### Scenario: Policy changes after call creation

- **WHEN** the caller policy is disabled or the bound owner leaves its allowed set before sideband attachment
- **THEN** attachment fails closed without selecting a replacement account
- **AND** no upstream sideband connection is attempted

### Requirement: Private realtime request logging supports callers without API keys

Private Live request logging MUST preserve the existing credential-safe content exclusions and typed `realtime_live`/`websocket` classification. Key callers MUST retain their existing `api_key_id`. OAuth callers MUST persist `api_key_id = NULL` without synthesizing `ApiKeyData`, and dashboard plus usage-rollup readers MUST consume those rows without schema or aggregation failure.

#### Scenario: OAuth sideband log has nullable key attribution

- **WHEN** a verified OAuth sideband completes or fails
- **THEN** one credential-safe `realtime_live` WebSocket row is persisted with `api_key_id = NULL`
- **AND** request-log API parsing and usage rollup folding succeed

### Requirement: Private realtime compatibility preserves zero-config base behavior and explicit client routing

The capability MUST add no global setting, required environment variable, dashboard navigation item, README section, or public Realtime endpoint. Absent OAuth policies leave existing base proxy/dashboard and registered-Key Live behavior unchanged. Documentation MUST scope keyless behavior to WebRTC Live Voice and identify the current first-party client overrides: `experimental_realtime_webrtc_call_base_url` targets the Codex-LB `/backend-api/codex` base and `experimental_realtime_ws_base_url` targets the `/v1` base so sideband reaches `/v1/realtime`.

#### Scenario: Operator does not enable OAuth Live Voice

- **WHEN** an operator upgrades without creating an OAuth Live policy
- **THEN** ordinary proxy/dashboard and registered-Key Live behavior remain available with zero new configuration
- **AND** OAuth Live routes remain fail closed

