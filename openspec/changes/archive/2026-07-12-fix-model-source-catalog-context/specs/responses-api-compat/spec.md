# responses-api-compat — Delta

## ADDED Requirements

### Requirement: Source-routed Responses tools are capability-filtered

When forwarding a Responses request to an OpenAI-compatible source, the proxy MUST forward `function` tools unchanged and MUST drop non-`function` tools the
source model has not declared support for. A source model declares support in
its `raw_metadata_json`: `"supports_search_tool": true` keeps web-search tools
(`web_search`, including the `web_search_preview` alias), and
`"experimental_supported_tools"` MAY list additional supported tool types.
When only some tools are dropped, a `tool_choice` that references a dropped
tool MUST be removed so the forwarded payload never names a tool that is not
present; `function`-typed choices MUST be preserved. When all tools are
dropped, `tools`, `tool_choice`, and `parallel_tool_calls` MUST be removed
together. Whenever a hosted tool is dropped, `include` entries specific to
that tool type (for example `web_search_call.*` for `web_search`,
`file_search_call.*` for `file_search`, `code_interpreter_call.*` for
`code_interpreter`, and `computer_call_output.*` for computer-use tools) MUST
be pruned from the forwarded payload; non-tool-specific entries (for example
`reasoning.encrypted_content`) MUST be kept, and the `include` field MUST be
removed entirely when pruning empties it. This filtering MUST apply on every
source-routed Responses surface (`/backend-api/codex/responses` and
`/v1/responses`).

#### Scenario: Codex-only tools are dropped for a plain source model

- **GIVEN** a Responses-capable source model with no tool capability opt-ins
- **WHEN** a Responses request with a `function` tool, a `namespace` tool, and a `web_search` tool is forwarded to it
- **THEN** the forwarded payload contains only the `function` tool

#### Scenario: Search-capable source models keep web-search tools

- **GIVEN** a source model whose `raw_metadata_json` sets `"supports_search_tool": true`
- **WHEN** a Responses request with a `function` tool and a `web_search` tool is forwarded to it
- **THEN** the forwarded payload contains both tools
- **AND** a `tool_choice` of `{"type": "web_search"}` is preserved

#### Scenario: tool_choice referencing a dropped tool is removed

- **GIVEN** a source model with no tool capability opt-ins
- **WHEN** a Responses request with a `function` tool, a `web_search` tool, and `tool_choice` `{"type": "web_search"}` is forwarded to it
- **THEN** the forwarded payload contains only the `function` tool
- **AND** the forwarded payload contains no `tool_choice` key

#### Scenario: include entries of a dropped tool are pruned

- **GIVEN** a source model with no tool capability opt-ins
- **WHEN** a Responses request with a `function` tool, a `web_search` tool, and `include` `["web_search_call.action.sources", "reasoning.encrypted_content"]` is forwarded to it
- **THEN** the forwarded payload contains only the `function` tool
- **AND** the forwarded payload's `include` contains only `"reasoning.encrypted_content"`

#### Scenario: Dropping every tool removes the tool-only fields

- **GIVEN** a source model with no tool capability opt-ins
- **WHEN** a Responses request whose tools are all unsupported is forwarded to it
- **THEN** the forwarded payload contains no `tools`, `tool_choice`, or `parallel_tool_calls` keys

### Requirement: Source request overrides apply without clobbering proxy-owned keys

When forwarding a Responses request to an OpenAI-compatible source, the proxy MUST apply the model's `source_request_overrides` from `raw_metadata_json` to
the forwarded payload. The `options` override MUST merge key-wise into any
client-sent `options` object, with override values winning per key. The
overrides MUST NOT change the `model` key (owned by source selection) or the
`stream` key (owned by the proxy's response-handling mode).

#### Scenario: Ollama options are injected into the forwarded payload

- **GIVEN** a source model whose overrides are `{"options": {"num_ctx": 32768}}`
- **WHEN** a Responses request is forwarded to the source
- **THEN** the forwarded payload contains `"options": {"num_ctx": 32768}`

#### Scenario: model and stream overrides are ignored

- **GIVEN** a source model whose overrides contain `"model": "other-model"` and `"stream": false`
- **WHEN** a streaming Responses request for slug `local-model` is forwarded to the source
- **THEN** the forwarded payload keeps `model` as the routed source model
- **AND** the forwarded payload keeps `stream` as `true`
