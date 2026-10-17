## ADDED Requirements

### Requirement: Embeddings source forwarding preserves field presence

For source-routed `POST /v1/embeddings` requests, the system MUST preserve both the values and presence of fields beyond the validated `model` and `input` fields. A field explicitly supplied as null MUST be forwarded as null, a field omitted by the client MUST remain absent, and a non-null field MUST be forwarded unchanged. This forwarding behavior MUST NOT change reservation settlement or request-log metadata.

#### Scenario: explicit null extras remain present

- **WHEN** a client supplies `dimensions: null` and `user: null` in a source-routed embeddings request
- **THEN** the compatible source receives both keys with null values

#### Scenario: omitted extras remain absent

- **WHEN** a client omits `dimensions` and `user` from a source-routed embeddings request
- **THEN** the compatible source payload does not contain either key

#### Scenario: non-null extras and accounting remain unchanged

- **WHEN** a client supplies non-null embedding extras through a limited API key
- **THEN** the compatible source receives those values unchanged
- **AND** the reservation settles from reported usage
- **AND** the successful request log retains its model-source metadata and token counts
