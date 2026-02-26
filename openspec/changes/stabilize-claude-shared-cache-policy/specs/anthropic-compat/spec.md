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
