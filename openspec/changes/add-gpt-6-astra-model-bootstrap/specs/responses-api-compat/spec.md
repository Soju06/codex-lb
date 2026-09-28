## ADDED Requirements

### Requirement: Cursor GPT-6 Astra model aliases normalize to canonical slug

For Responses proxy traffic, the service MUST recognize Cursor-style model
aliases formed by appending known suffix tokens (`minimal`, `low`, `medium`,
`high`, `xhigh`, `extra`, `fast`, `priority`, `reasoning`, `thinking`) to the
`gpt-6-astra` slug. The resolver MUST normalize the model to `gpt-6-astra` and
move recognized reasoning and speed labels into request fields. Unknown suffix
tokens MUST leave the requested model unchanged. `ultra` and `max` remain
unsupported model-name suffix tokens and MUST pass through unchanged.

#### Scenario: GPT-6 Astra alias normalizes reasoning and service tier

- **WHEN** a client sends a Responses request with `model: "gpt-6-astra-extra-high-fast"`
- **THEN** the forwarded upstream request uses `model: "gpt-6-astra"`
- **AND** the forwarded upstream request uses `reasoning.effort: "high"`
- **AND** the forwarded upstream request uses `service_tier: "priority"`

#### Scenario: GPT-6 Astra ultra-suffixed label is not rewritten

- **WHEN** a client sends a Responses request with `model: "gpt-6-astra-ultra"`
- **THEN** the forwarded upstream request keeps `model: "gpt-6-astra-ultra"`
