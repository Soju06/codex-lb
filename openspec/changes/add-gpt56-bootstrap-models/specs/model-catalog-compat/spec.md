## MODIFIED Requirements

### Requirement: Bootstrap model catalog is available before refresh

Before the first successful upstream model-registry refresh, the system MUST
serve a conservative static catalog of known Codex model slugs from both
`GET /v1/models` and `GET /backend-api/codex/models`. This static catalog is a
bundled fallback for startup/offline paths; refreshed upstream model-registry
data remains the authoritative source once available. The bootstrap catalog MUST
include `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.5`,
`gpt-5.4`, `gpt-5.4-mini`, `gpt-5.3-codex`, `gpt-5.3-codex-spark`,
`gpt-5.2`, and `codex-auto-review`, and MUST NOT invent unverified variant
slugs such as `gpt-5.5-pro` or a bare `gpt-5.6`.

#### Scenario: OpenAI-compatible models endpoint serves bootstrap slugs

- **GIVEN** the model registry has no refreshed upstream snapshot
- **WHEN** a client calls `GET /v1/models`
- **THEN** the response contains exactly the bootstrap model slugs
- **AND** the response includes `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna`
- **AND** the response does not include `gpt-5.5-pro` or bare `gpt-5.6`

#### Scenario: Codex-native models endpoint serves GPT-5.6 bootstrap metadata

- **GIVEN** the model registry has no refreshed upstream snapshot
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** the `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna` entries mirror the contract-relevant upstream metadata, including `minimal_client_version: "0.144.0"`, `tool_mode: "code_mode_only"`, Responses-Lite, context-window, visibility, speed-tier, plan-availability, and reasoning fields
- **AND** Sol and Terra advertise multi-agent metadata version `v2`
- **AND** Luna advertises multi-agent metadata version `v1`
- **AND** Sol and Terra advertise `low`, `medium`, `high`, `xhigh`, `max`, and `ultra`
- **AND** Luna advertises `low`, `medium`, `high`, `xhigh`, and `max`

## ADDED Requirements

### Requirement: Dashboard model metadata exposes supported reasoning efforts

When serving `GET /api/models`, the system MUST expose the supported reasoning
efforts advertised by each public model catalog entry. The response MUST include
new upstream-supported efforts such as `max` and `ultra` instead of filtering
them out.

#### Scenario: Dashboard model list exposes GPT-5.6 reasoning efforts

- **WHEN** the model catalog contains `gpt-5.6-sol` with supported efforts `low`, `medium`, `high`, `xhigh`, `max`, and `ultra`
- **WHEN** a client calls `GET /api/models`
- **THEN** the `gpt-5.6-sol` entry's `supportedReasoningEfforts` includes `max` and `ultra`
- **AND** `defaultReasoningEffort` reflects the catalog default
