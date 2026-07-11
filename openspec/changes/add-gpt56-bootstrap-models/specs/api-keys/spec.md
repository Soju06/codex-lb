## ADDED Requirements

### Requirement: API keys can enforce extended reasoning efforts

The dashboard API key CRUD surface MUST allow callers to persist optional
enforced reasoning efforts advertised by the model catalog, including extended
GPT-5.6 efforts `max` and `ultra`.

#### Scenario: API key accepts extended enforced reasoning effort on create

- **WHEN** a dashboard client creates an API key with `enforcedReasoningEffort: "ultra"`
- **THEN** the request is accepted
- **AND** the response returns `enforcedReasoningEffort: "ultra"`

#### Scenario: API key accepts extended enforced reasoning effort on update

- **WHEN** a dashboard client updates an API key with `enforcedReasoningEffort: "max"`
- **THEN** the request is accepted
- **AND** the response returns `enforcedReasoningEffort: "max"`
