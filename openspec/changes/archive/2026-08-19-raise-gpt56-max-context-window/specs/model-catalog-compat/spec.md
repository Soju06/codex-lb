## MODIFIED Requirements

### Requirement: GPT-5.6 bootstrap metadata matches the upstream bundled catalog

The GPT-5.6 bootstrap catalog entries (`gpt-5.6-sol`, `gpt-5.6-terra`,
`gpt-5.6-luna`) MUST mirror the upstream bundled catalog
(`codex-rs/models-manager/models.json` at Codex release `rust-v0.145.0`)
field-for-field for every metadata field codex-lb serves, with one tracked
exception: `max_context_window`, which upstream raised from `272000` to
`872000` in openai/codex commit
`2eee483e49f88b868f67364134a658b3298e6c14` (openai/codex#39102) and which no
`rust-v*` release tag carries as of `rust-v0.148.0-alpha.21`. In particular
each entry MUST carry: `context_window` of `272000` and `max_context_window`
of `872000`; `minimal_client_version` `"0.144.0"`; `tool_mode`
`"code_mode_only"`; `use_responses_lite` `true`; `apply_patch_tool_type`
`"freeform"`; `web_search_tool_type` `"text_and_image"`;
`supports_image_detail_original` `true`; `truncation_policy` `{ "mode":
"tokens", "limit": 10000 }`; `comp_hash` `"3000"`; `reasoning_summary_format`
`"experimental"`; `default_reasoning_summary` `"none"`;
`include_skills_usage_instructions` `false`; `experimental_supported_tools`
`[]` (a field the Codex client's deserializer requires); `supports_search_tool`
`true`; `additional_speed_tiers` `["fast"]`; the `priority`/`Fast` service tier
entry; `shell_type` `"shell_command"`; `prefer_websockets` `true`; and the
21-plan `available_in_plans` list upstream advertises (including `edu_plus`,
`edu_pro`, `enterprise_cbp_automation`, and `sci`). `multi_agent_version` MUST
be `"v2"` for Sol and Terra and `"v1"` for Luna. Sol MUST carry the upstream
`availability_nux` message while Terra and Luna carry `null`. Default reasoning
levels MUST be `low` for Sol and `medium` for Terra and Luna, and
reasoning-level descriptions MUST be the verbatim upstream strings.

`context_window` is the default input budget and `max_context_window` is the
ceiling a client may opt into; the two MUST NOT be collapsed into one value
for these entries.

The ~16.5 KB upstream `base_instructions` prompt and the personality-templated
`model_messages` object are deliberately NOT bundled in the bootstrap catalog;
the first successful live registry refresh supplies them. This is the only
sanctioned divergence from the upstream GPT-5.6 entries beyond the
`max_context_window` exception above.

#### Scenario: GPT-5.6 bootstrap entries retain the corrected upstream context budget

- **GIVEN** the model registry has no refreshed upstream snapshot
- **AND** no persisted snapshot is loaded
- **AND** no `CODEX_LB_MODEL_CONTEXT_WINDOW_OVERRIDES` entry applies to these slugs
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna` report
  `context_window=272000`

#### Scenario: GPT-5.6 bootstrap entries advertise the raised upstream ceiling

- **GIVEN** the model registry has no refreshed upstream snapshot
- **AND** no persisted snapshot is loaded
- **AND** no `CODEX_LB_MODEL_CONTEXT_WINDOW_OVERRIDES` entry applies to these slugs
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna` report
  `context_window=272000`
- **AND** each reports `max_context_window=872000`

#### Scenario: OpenAI-compatible metadata keeps the default input budget

- **GIVEN** the model registry has no refreshed upstream snapshot
- **AND** no persisted snapshot is loaded
- **AND** no `CODEX_LB_MODEL_CONTEXT_WINDOW_OVERRIDES` entry applies to these slugs
- **WHEN** a client calls `GET /v1/models`
- **THEN** each GPT-5.6 entry reports `context_window=272000` and
  `input_context_window=272000`
- **AND** the raised Codex-native ceiling is not promoted into the
  OpenAI-compatible input budget fields

#### Scenario: GPT-5.6 entries expose upstream tool and multi-agent metadata

- **GIVEN** the model registry has no refreshed upstream snapshot
- **AND** no persisted snapshot is loaded
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna` carry `tool_mode: "code_mode_only"`, `use_responses_lite: true`, `experimental_supported_tools: []`, and `minimal_client_version: "0.144.0"`
- **AND** `multi_agent_version` is `"v2"` for Sol and Terra and `"v1"` for Luna

#### Scenario: GPT-5.6 entries expose upstream reasoning-summary and plan metadata

- **GIVEN** the model registry has no refreshed upstream snapshot
- **AND** no persisted snapshot is loaded
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** each GPT-5.6 entry carries `default_reasoning_summary: "none"`, `reasoning_summary_format: "experimental"`, and `comp_hash: "3000"`
- **AND** each GPT-5.6 entry's `available_in_plans` includes `edu_plus`, `edu_pro`, `enterprise_cbp_automation`, and `sci`
- **AND** only `gpt-5.6-sol` carries a non-null `availability_nux` message
