## ADDED Requirements

### Requirement: Automatic Daybreak request recognition
For an opted-in authenticated key, Responses WebSocket, HTTP Responses and compact requests MUST require Trusted Cyber accounts for `gpt-daybreak-blue-latest` or native `access_programs.cyber = "daybreak_blue"`. It MUST preserve native fields, key scope, ownership and inherited constraints. Ordinary requests MUST use normal routing. Invalid native selectors MUST fail before dispatch. Opted-out keys MUST retain existing behavior.

#### Scenario: One key supports ordinary and Daybreak requests
- **GIVEN** a key permits ordinary and Trusted Cyber accounts and enables automatic routing
- **WHEN** a native Blue frame arrives without a custom marker
- **THEN** selection requires Trusted Cyber eligibility before opening upstream
- **AND** the upstream payload retains the native program

#### Scenario: Legacy and ordinary requests
- **WHEN** the same enabled key requests the legacy Daybreak model
- **THEN** it uses the restricted eligible pool
- **AND** a generic model without a Daybreak selector uses its ordinary pool

#### Scenario: HTTP Responses and compact
- **WHEN** an enabled key sends a legacy or native Daybreak request over HTTP Responses or compact
- **THEN** initial selection and all retries require an eligible Trusted Cyber account within the key scope
- **AND** native fields and account ownership are preserved
- **AND** an empty eligible pool fails without ordinary or external model-source dispatch

#### Scenario: Safe replay retains the requirement
- **WHEN** a verified fresh replay contains only a supported native access-program selector and otherwise account-neutral data
- **THEN** the selector does not itself prevent retry to another eligible account
- **AND** owned continuation data still prevents cross-account replay

#### Scenario: Invalid native selection
- **WHEN** an opted-in key sends a malformed, duplicate, conflicting or unsupported native selector
- **THEN** the request fails before upstream dispatch
- **AND** Red is not treated as Blue
- **AND** explicit standard conflicts with a legacy or custom Daybreak marker

#### Scenario: Equivalent HTTP paths
- **WHEN** a Daybreak request uses a supported Responses alias or a trailing slash on Responses or compact
- **THEN** the same native eligibility and forwarding contract applies

#### Scenario: API-key enforced Daybreak model
- **WHEN** an enabled key enforces `gpt-daybreak-blue-latest` on a generic Responses or compact request
- **THEN** selection and retries MUST require Trusted Cyber eligibility within the key scope
- **AND** an explicit native `standard` selector MUST fail as a conflict before dispatch
- **AND** unrelated WebSocket control frames without selectors MUST retain their existing behavior
