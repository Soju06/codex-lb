## MODIFIED Requirements

### Requirement: Image generation is implemented as a Responses tool adapter

The system SHALL implement `/v1/images/generations` and `/v1/images/edits` by
issuing an internal `/v1/responses` request whose `tools` array includes
`{"type": "image_generation", ...}`. The internal request MUST use a dedicated
ordered Images host selection that prefers a visible, unsuppressed
`gpt-5.6-sol` before fallback candidates and MUST remain independent from the
default account-probe host selection. The public `gpt-image-*` model MUST remain
in the image tool configuration and MUST NOT be replaced by the internal host
model.

#### Scenario: Images routes prefer an image-compatible host

- **GIVEN** `gpt-5.6-sol` is visible and unsuppressed in the model registry
- **WHEN** a client sends a valid generation or edit request
- **THEN** the internal Responses request uses `gpt-5.6-sol` as its host model
- **AND** the image tool retains the publicly requested `gpt-image-*` model

#### Scenario: Images host selection does not change account probes

- **WHEN** Images host preference changes
- **THEN** the default account-probe host order remains unchanged
