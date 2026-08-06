## ADDED Requirements

### Requirement: Chat Completions shares API-key reasoning allowlist enforcement

Before Chat Completions traffic selects a subscription account or an external
model source, the service MUST convert reasoning controls to the internal
Responses representation and apply the authenticated API key's
`allowedReasoningEfforts` policy. A rejected effort MUST produce the same
OpenAI-compatible `403` `reasoning_effort_not_allowed` result as a native
Responses request and MUST NOT call the external source.

After a source-routed Chat Completions request passes the policy, any accepted
`ultra` value MUST use the upstream wire value `max` regardless of whether the
client expressed it through `reasoning_effort`, `reasoningEffort`,
`reasoning.effort`, or `thinking`. If several reasoning spellings conflict,
every retained outbound spelling MUST be aligned to the effort authorized from
the converted Responses request.

#### Scenario: Source-routed chat request is rejected before forwarding

- **GIVEN** a source-routed chat model and an API key with
  `allowedReasoningEfforts: ["low", "medium", "high"]`
- **WHEN** a Chat Completions client supplies `reasoning_effort: "ultra"`
- **THEN** the service returns `403` with code `reasoning_effort_not_allowed`
- **AND** the source receives no request

#### Scenario: Source-routed chat aliases use the ultra wire value

- **GIVEN** a source-routed chat model and an API key that allows `ultra`
- **WHEN** a Chat Completions client supplies `thinking: "ultra"`
- **THEN** the source receives `thinking: "max"`
