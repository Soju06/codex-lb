## MODIFIED Requirements

### Requirement: Mixed-provider routing policy is explicit

The system MUST expose an explicit route-family eligibility policy for each upstream identity. `openai_platform` identities are opt-in for a fixed enum of eligible route families, are fallback-only behind the existing ChatGPT pool, and are not silently merged into unsupported ChatGPT-private pools.

#### Scenario: Platform identity is eligible only for enabled route families
- **WHEN** an operator enables a subset of route families for an `openai_platform` identity
- **THEN** the router considers that identity only for those route families
- **AND** it continues excluding the identity from unsupported websocket, compact, and continuity-dependent behavior

#### Scenario: Codex backend HTTP route family is independently controllable
- **WHEN** the system exposes route-family eligibility controls for `openai_platform`
- **THEN** the supported enum includes `backend_codex_http`
- **AND** operators may enable `backend_codex_http` without enabling public `/v1/*` route families

#### Scenario: Platform becomes fallback for backend Codex HTTP when the compatible ChatGPT pool has no healthy candidates
- **WHEN** a request targets HTTP `/backend-api/codex/models` or stateless HTTP `/backend-api/codex/responses`
- **AND** both `chatgpt_web` and `openai_platform` identities are configured for `backend_codex_http`
- **AND** no compatible ChatGPT-web candidate remains healthy under the configured fallback thresholds
- **THEN** the service MAY select the Platform identity as fallback for that request
