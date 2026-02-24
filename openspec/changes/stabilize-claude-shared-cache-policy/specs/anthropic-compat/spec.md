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
