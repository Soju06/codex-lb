## MODIFIED Requirements

### Requirement: Source-routed Responses tools are capability-filtered

When forwarding a Responses request to an OpenAI-compatible source, the proxy MUST forward `function` tools unchanged and MUST drop non-`function` tools the
source model has not declared support for. A source model declares support in
its `raw_metadata_json`: `"supports_search_tool": true` keeps web-search tools
(`web_search`, including the `web_search_preview` alias), and
`"experimental_supported_tools"` MAY list additional supported tool types.
A source model that declares `"tool_mode": "code_mode_only"` or
`"apply_patch_tool_type": "freeform"` MUST also be treated as supporting
`custom` tools. Custom tool definitions (including grammar), matching named
or `allowed_tools` choices, and `parallel_tool_calls` MUST be preserved when
those tools survive filtering. A model with neither declaration nor an
explicit `custom` opt-in MUST continue to drop custom tools.
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

#### Scenario: Code-mode sources retain execution tools

- **GIVEN** a source model declares `tool_mode` as `code_mode_only` without listing `custom` in `experimental_supported_tools`
- **WHEN** a request contains the custom `exec` tool, function `wait` tool, and an unsupported hosted tool
- **THEN** the custom and function definitions, including the custom grammar, are forwarded unchanged
- **AND** choices naming the retained custom tool and `parallel_tool_calls` are preserved
- **AND** the unsupported hosted tool is dropped

#### Scenario: Freeform apply-patch sources retain custom tools

- **GIVEN** a source model declares `apply_patch_tool_type` as `freeform` without listing `custom` in `experimental_supported_tools`
- **WHEN** a request contains a custom `apply_patch` tool with a matching named or allowed-tools choice
- **THEN** the custom definition and matching choice are forwarded unchanged

#### Scenario: Plain source models still require an explicit custom opt-in

- **GIVEN** a source model declares neither code mode nor freeform apply-patch and has no explicit `custom` opt-in
- **WHEN** a request contains a custom tool, a function tool, and a choice naming the custom tool
- **THEN** the custom tool and its dangling choice are removed while the function tool is retained
