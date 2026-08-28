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
slugs such as `gpt-5.5-pro` or a bare `gpt-5.6`. `gpt-5.3-codex` and
`gpt-5.3-codex-spark` were dropped from upstream's bundled catalog at
codex rust-v0.144.x but remain retained for older pinned clients because the
upstream backend still serves them.

#### Scenario: OpenAI-compatible models endpoint serves bootstrap slugs

- **GIVEN** the model registry has no refreshed upstream snapshot
- **WHEN** a client calls `GET /v1/models`
- **THEN** the response contains exactly the bootstrap model slugs
- **AND** the response includes `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna`
- **AND** the response does not include `gpt-5.5-pro` or bare `gpt-5.6`

#### Scenario: Codex-native models endpoint serves GPT-5.6 bootstrap metadata

- **GIVEN** the model registry has no refreshed upstream snapshot
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** the `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna` entries include representative upstream metadata including context-window, visibility, speed-tier, and reasoning fields
- **AND** Sol and Terra advertise `low`, `medium`, `high`, `xhigh`, `max`, and `ultra`
- **AND** Luna advertises `low`, `medium`, `high`, `xhigh`, and `max`

## ADDED Requirements

### Requirement: GPT-5.6 bootstrap metadata matches the upstream bundled catalog

The GPT-5.6 bootstrap catalog entries (`gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`) MUST mirror the upstream bundled catalog (`codex-rs/models-manager/models.json` at codex release rust-v0.144.1) field-for-field for every metadata field codex-lb serves. In particular each
entry MUST carry: `context_window` and `max_context_window` of `372000`;
`minimal_client_version` `"0.144.0"`; `tool_mode` `"code_mode_only"`;
`use_responses_lite` `true`; `apply_patch_tool_type` `"freeform"`;
`web_search_tool_type` `"text_and_image"`; `supports_image_detail_original`
`true`; `truncation_policy` `{"mode": "tokens", "limit": 10000}`;
`comp_hash` `"3000"`; `reasoning_summary_format` `"experimental"`;
`default_reasoning_summary` `"none"`; `include_skills_usage_instructions`
`false`; `experimental_supported_tools` `[]` (a field the Codex client's
deserializer requires); `supports_search_tool` `true`; `additional_speed_tiers`
`["fast"]`; the `priority`/`Fast` service tier entry; `shell_type`
`"shell_command"`; `prefer_websockets` `true`; and the 21-plan
`available_in_plans` list upstream advertises (including `edu_plus`,
`edu_pro`, `enterprise_cbp_automation`, and `sci`). `multi_agent_version` MUST
be `"v2"` for Sol and Terra and `"v1"` for Luna. Sol MUST carry the upstream
`availability_nux` message while Terra and Luna carry `null`. Default
reasoning levels MUST be `low` for Sol and `medium` for Terra and Luna, and
reasoning-level descriptions MUST be the verbatim upstream strings.

The ~16.5 KB upstream `base_instructions` prompt and the personality-templated
`model_messages` object are deliberately NOT bundled in the bootstrap catalog;
the first successful live registry refresh supplies them. This is the only
sanctioned divergence from the upstream GPT-5.6 entries.

#### Scenario: GPT-5.6 entries expose upstream tool and multi-agent metadata

- **GIVEN** the model registry has no refreshed upstream snapshot
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna` carry `tool_mode: "code_mode_only"`, `use_responses_lite: true`, `experimental_supported_tools: []`, and `minimal_client_version: "0.144.0"`
- **AND** `multi_agent_version` is `"v2"` for Sol and Terra and `"v1"` for Luna

#### Scenario: GPT-5.6 entries expose upstream reasoning-summary and plan metadata

- **GIVEN** the model registry has no refreshed upstream snapshot
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** each GPT-5.6 entry carries `default_reasoning_summary: "none"`, `reasoning_summary_format: "experimental"`, and `comp_hash: "3000"`
- **AND** each GPT-5.6 entry's `available_in_plans` includes `edu_plus`, `edu_pro`, `enterprise_cbp_automation`, and `sci`
- **AND** only `gpt-5.6-sol` carries a non-null `availability_nux` message

### Requirement: Fallback client version covers the bootstrap catalog

The configured fallback Codex client version (used when the live Codex release lookup fails and no cached version exists) MUST be greater than or equal to the highest `minimal_client_version` in the bootstrap catalog, so a degraded-startup registry refresh still receives the newest bootstrap models from upstream.

#### Scenario: Degraded-startup refresh still requests GPT-5.6

- **GIVEN** the live Codex release lookup fails and no version is cached
- **WHEN** the model registry refresh fetches `<base>/codex/models?client_version=<fallback>`
- **THEN** the fallback version is at least `0.144.0` (GPT-5.6's `minimal_client_version`)

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
