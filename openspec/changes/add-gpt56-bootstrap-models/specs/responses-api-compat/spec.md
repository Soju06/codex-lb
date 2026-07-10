## ADDED Requirements

### Requirement: Client-facing ultra reasoning canonicalizes to wire max

The proxy MUST preserve `ultra` as a client-facing model-catalog and policy
value while canonicalizing it to `max` before forwarding any Responses or
Compact request to an upstream, including OpenAI-compatible model sources. The
canonicalization MUST apply after API-key enforcement and to
automation-generated Compact requests.

#### Scenario: API-key ultra enforcement sends wire max

- **WHEN** an API key enforces `ultra` on a Responses request
- **THEN** the API-key policy remains `ultra`
- **AND** the forwarded upstream request uses `reasoning.effort: "max"`

#### Scenario: Automation ultra selection sends wire max

- **WHEN** an automation run is configured with `reasoningEffort: "ultra"`
- **THEN** the job and run metadata retain `ultra`
- **AND** the Compact request forwarded upstream uses `reasoning.effort: "max"`

#### Scenario: Model-source Responses aliases send wire max

- **WHEN** a source-routed Responses request includes `thinking: "ultra"` or `reasoningEffort: "ultra"`
- **THEN** the OpenAI-compatible source receives `reasoning.effort: "max"`
- **AND** the source payload does not include `thinking` or `reasoningEffort`

## MODIFIED Requirements

### Requirement: OpenAI-compatible Responses payload sanitation removes provider-specific thinking aliases

The shared OpenAI-compatible Responses sanitation path MUST normalize
third-party thinking aliases into the canonical `reasoning` object before
upstream forwarding. String and object aliases using `max` MUST remain `max`,
while aliases using client-facing `ultra` MUST normalize to wire-level `max`.
Unknown provider-specific thinking controls MUST NOT be passed through
unchanged to the upstream ChatGPT backend.

#### Scenario: Shared payload sanitation maps enable_thinking

- **WHEN** an internal Responses payload contains `enable_thinking: true`
- **AND** no explicit `reasoning.effort` is already present
- **THEN** the forwarded upstream payload includes `reasoning.effort: "medium"`
- **AND** the forwarded upstream payload does not include `enable_thinking`

#### Scenario: Explicit reasoning wins over provider aliases

- **WHEN** an internal Responses payload contains both `reasoning: {"effort":"high"}` and `thinking: {"type":"enabled"}`
- **THEN** the forwarded upstream payload keeps `reasoning.effort: "high"`
- **AND** the forwarded upstream payload does not include `thinking`

#### Scenario: Max thinking alias remains max

- **WHEN** an internal Responses payload contains `thinking: "max"`
- **AND** no explicit `reasoning.effort` is already present
- **THEN** the forwarded upstream payload includes `reasoning.effort: "max"`
- **AND** the forwarded payload does not include `thinking`

#### Scenario: Ultra thinking alias normalizes to wire max

- **WHEN** an internal Responses payload contains `thinking: "ultra"`
- **AND** no explicit `reasoning.effort` is already present
- **THEN** the forwarded upstream payload includes `reasoning.effort: "max"`
- **AND** the forwarded payload does not include `thinking`
