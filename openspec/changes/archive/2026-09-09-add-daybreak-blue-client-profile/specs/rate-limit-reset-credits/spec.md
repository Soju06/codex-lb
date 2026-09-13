## ADDED Requirements

### Requirement: Daybreak capability intent cannot downgrade through reset-credit routing

`POST /v1/reset-credit` and `POST /api/codex/rate-limit-reset-credits/consume` (with or without its trailing slash) MUST require a valid proxy API key whenever `X-Codex-LB-Required-Capability` is present. After authentication they MUST return HTTP 400 with `error.code = "required_capability_transport_unsupported"` before account lookup, ChatGPT usage-identity validation, credential decryption, upstream route resolution, reset-credit fetch, or reset-credit consume. Headerless requests MUST retain their existing authentication and redemption behavior. Capability-bearing reads of `/api/codex/usage`, `/v1/usage`, and `/v1/reset-credit` MAY remain available after proxy API-key authentication because their API-key paths are local and do not select an upstream account or dispatch an upstream request. They MUST NOT enter ChatGPT usage-identity validation while the carrier is present.

#### Scenario: Authenticated reset-credit carrier fails before account routing

- **WHEN** a valid proxy API key sends either reset-credit consume surface with the Daybreak carrier
- **THEN** ingress returns HTTP 400 `required_capability_transport_unsupported`
- **AND** no account, ChatGPT identity, credential, route, fetch, or consume operation is reached

#### Scenario: Local usage initialization authenticates without upstream identity lookup

- **WHEN** a valid proxy API key reads a local usage or reset-credit listing with the Daybreak carrier
- **THEN** the existing local API-key response remains available
- **AND** no ChatGPT usage-identity request or upstream account routing occurs

#### Scenario: Headerless reset-credit behavior remains unchanged

- **WHEN** a reset-credit request omits the required-capability carrier
- **THEN** the existing API-key or ChatGPT identity authentication and redemption behavior remains in effect
