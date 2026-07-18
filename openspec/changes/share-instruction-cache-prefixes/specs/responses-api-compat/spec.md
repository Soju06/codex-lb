## ADDED Requirements

### Requirement: Codex requests may share stable instruction cache prefixes

When shared instruction caching is enabled, the service MUST derive an upstream
`prompt_cache_key` for Codex-native Responses requests from the exact
model and exact request prefix through a structurally identified stable
breakpoint. The service MUST preserve the client-supplied cache key for local
session affinity, bridge identity, continuation, and request isolation. The
service MUST NOT apply this policy to OpenAI-style routes, requests without a
stable prefix boundary, or requests that already contain a client-authored
explicit breakpoint. It MUST add explicit breakpoints only for models known to
support them and retain automatic caching for older models.

#### Scenario: Two Sol threads share a stable prefix

- **GIVEN** shared instruction caching is enabled
- **AND** two Codex-native Sol requests have different client cache keys
- **AND** their request prefixes through the contextual user message are exact matches
- **WHEN** the requests are forwarded upstream
- **THEN** they use the same upstream cache key and explicit breakpoint
- **AND** their local bridge identities remain different

#### Scenario: Model variants remain cache-isolated

- **GIVEN** Sol, Terra, and Luna requests contain the same stable instruction prefix
- **WHEN** CodexLB derives their upstream cache keys
- **THEN** every exact model receives a distinct key

#### Scenario: Older models use automatic caching

- **GIVEN** a pre-GPT-5.6 Codex request contains a stable instruction prefix
- **WHEN** CodexLB derives its shared upstream cache key
- **THEN** it does not add an explicit breakpoint
- **AND** the upstream model retains its automatic caching behavior

#### Scenario: Prefix changes produce a new key

- **GIVEN** two requests use the same GPT-5.6 model
- **WHEN** any instruction, tool definition, or input content before the breakpoint differs
- **THEN** CodexLB derives different upstream cache keys

#### Scenario: Unsupported upstream cache policy is omitted

- **WHEN** CodexLB adds a shared instruction breakpoint for the ChatGPT Codex backend
- **THEN** it MUST NOT add `prompt_cache_options`
- **AND** the backend's implicit cache policy remains in effect
