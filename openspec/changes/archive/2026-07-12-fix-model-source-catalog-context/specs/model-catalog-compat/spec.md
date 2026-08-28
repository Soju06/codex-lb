# model-catalog-compat — Delta

## ADDED Requirements

### Requirement: Source-model Codex catalog entries are Codex-parseable

Codex catalog entries built for OpenAI-compatible source models MUST be
parseable by Codex clients without relying on bundled metadata. When the
source model has no configured context window, the entry MUST report a
context window of 128,000 tokens and a matching `max_context_window`. The
entry MUST include `shell_type` (`shell_command`), a `truncation_policy`,
and the client-capability fields `include_skills_usage_instructions`,
`supports_image_detail_original`, `supports_search_tool`,
`use_responses_lite`, and `experimental_supported_tools`, defaulting each to
its most conservative value. Operator-provided values for these keys in the
source model's `raw_metadata_json` MUST take precedence over the defaults.

#### Scenario: Source model without a context window gets the default budget

- **GIVEN** an enabled Responses-capable source model with no `contextWindow` configured
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** the model's entry reports `context_window` of 128000
- **AND** `max_context_window` of 128000
- **AND** `shell_type` of `shell_command`
- **AND** conservative defaults for the client-capability fields (for example `supports_search_tool` is `false` and `use_responses_lite` is `false`)

#### Scenario: Operator capability opt-in overrides the defaults

- **GIVEN** a source model whose `raw_metadata_json` sets `"supports_search_tool": true`
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** the model's entry reports `supports_search_tool` as `true`

### Requirement: Source request overrides never appear in client-visible catalogs

The per-model `source_request_overrides` object in a source model's `raw_metadata_json` is operator-side request configuration and MUST NOT
appear in any client-visible catalog payload (`GET /backend-api/codex/models`,
`GET /v1/models`, or any equivalent catalog route), while remaining available
server-side for request override application.

#### Scenario: Override config is stripped from the Codex catalog

- **GIVEN** a source model whose `raw_metadata_json` contains `"source_request_overrides": {"options": {"num_ctx": 32768}}`
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** the model's catalog entry does not contain a `source_request_overrides` key
- **AND** the string `source_request_overrides` appears nowhere in the response payload

#### Scenario: Overrides still apply to forwarded requests

- **GIVEN** the same source model
- **WHEN** a Responses request is forwarded to the source
- **THEN** the forwarded payload includes the configured override values
