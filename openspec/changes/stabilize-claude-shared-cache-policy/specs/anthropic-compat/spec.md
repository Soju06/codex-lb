## ADDED Requirements

### Requirement: Stable Claude shared cache key for non-explicit sources
For Claude-compatible requests, the system SHALL preserve caller-provided prompt cache keys only when the source is `explicit`. For all non-explicit sources (`metadata`, `cache_control`, `anchor`, `none`), the system SHALL replace the derived key with a deterministic `claude-shared:*` key.

#### Scenario: Cache-control source in Claude request
- **WHEN** a Claude request derives prompt cache key from `cache_control`
- **THEN** the forwarded key is `claude-shared:*` and not `anthropic-cache:*`

#### Scenario: Metadata source in Claude request
- **WHEN** a Claude request derives prompt cache key from metadata
- **THEN** the forwarded key is `claude-shared:*`

#### Scenario: Explicit source in Claude request
- **WHEN** a Claude request includes explicit `prompt_cache_key`
- **THEN** the forwarded key is preserved exactly as provided

### Requirement: Anthropic-compatible transport does not force model overrides
The Anthropic-compatible layer SHALL not silently remap requested models (including `claude-*`) to hardcoded OpenAI model IDs, and SHALL preserve caller sampling controls when forwarding requests.

#### Scenario: Claude model request is forwarded without forced aliasing
- **WHEN** a Claude-compatible request specifies model `claude-sonnet-4-6`
- **THEN** the forwarded upstream request model remains `claude-sonnet-4-6`
- **AND** sampling fields such as `temperature`, `top_p`, and `top_k` remain unchanged

### Requirement: Anthropic-compatible requests map reasoning effort aliases
The Anthropic-compatible translation layer SHALL map Claude-facing reasoning effort aliases into `ResponsesRequest.reasoning.effort`.

#### Scenario: Top-level reasoningEffort alias is provided
- **WHEN** a request includes `reasoningEffort` with a non-empty string value
- **THEN** the translated upstream payload includes `reasoning.effort` with that value

#### Scenario: Nested reasoning.effort is provided
- **WHEN** a request includes `reasoning` object with non-empty `effort`
- **THEN** the translated upstream payload includes `reasoning.effort` with that value

### Requirement: Anthropic-compatible routes support server default reasoning effort
The Anthropic-compatible API layer SHALL apply a configured server default reasoning effort when the request does not provide one.

#### Scenario: Default reasoning effort is configured and request has no explicit effort
- **WHEN** `CODEX_LB_ANTHROPIC_DEFAULT_REASONING_EFFORT` is set to `xhigh`
- **AND** a request omits both `reasoningEffort` and `reasoning.effort`
- **THEN** forwarded request payload includes `reasoning.effort` equal to `xhigh`

#### Scenario: Explicit request effort takes precedence over configured default
- **WHEN** `CODEX_LB_ANTHROPIC_DEFAULT_REASONING_EFFORT` is set
- **AND** a request includes `reasoningEffort` or `reasoning.effort`
- **THEN** forwarded request payload uses the request-provided effort value
