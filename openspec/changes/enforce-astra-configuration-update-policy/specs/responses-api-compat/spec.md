## ADDED Requirements

### Requirement: Astra configuration updates preserve compatible history

For subscription-backed Astra, the proxy SHALL preserve supported
`configuration_update` input items and normalize client-plane ultra to max
at subscription wire serialization. It SHALL reject unsupported reasoning
values and invalid adjacent updates before upstream work. Histories with
configuration updates SHALL reject automatic compaction, automatic
truncation, and the standalone compact endpoint. Normal request-level
reasoning SHALL remain unchanged by a valid input update. Explicit
terminal compaction_trigger items combined with configuration updates
SHALL remain on the Responses endpoint instead of being converted to a
standalone compact request. Astra-specific model restrictions SHALL NOT
be applied to externally configured model sources sharing the same model
ID.

#### Scenario: A valid update preserves the request cache prefix

- **WHEN** a request has request-level low reasoning and a high configuration_update between conversation messages
- **THEN** the forwarded request retains low at request level and high in that input item

#### Scenario: Explicit compaction retains configuration history

- **WHEN** a subscription Astra Responses request contains configuration updates and one terminal compaction_trigger
- **THEN** the proxy forwards the updates and trigger on the Responses path without calling the standalone compact endpoint

#### Scenario: A source with the same model name owns its model contract

- **GIVEN** a request routes to an externally configured model source named gpt-6-astra
- **WHEN** that source supports its own reasoning levels, logprobs or configuration update schema
- **THEN** subscription-specific Astra validation does not override that contract
- **AND** API-key reasoning policy remains enforced before source forwarding

#### Scenario: Configuration updates cannot use standalone compaction

- **WHEN** a compact request contains a configuration_update item
- **THEN** the proxy returns a compatible invalid-request error before upstream work
