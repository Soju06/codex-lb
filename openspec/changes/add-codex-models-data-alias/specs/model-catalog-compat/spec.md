## MODIFIED Requirements

### Requirement: Native Codex model catalog stays backend-faithful

When serving `GET /backend-api/codex/models`, the system MUST keep Codex-native model catalog semantics unchanged: the top-level `context_window` field remains the backend compact/input budget unless an explicit operator override applies, and upstream raw fields such as `max_context_window` remain available when upstream provides them. The `/v1/models` compatibility metadata MUST NOT mutate the native Codex endpoint.

#### Scenario: Codex model catalog also exposes OpenAI data alias

- **WHEN** a client requests `GET /backend-api/codex/models`
- **THEN** the response keeps the Codex-native `models` list
- **AND** the response includes `object: "list"` and an OpenAI-compatible `data` list
- **AND** `data` contains model entries whose Codex visibility is `list`
- **AND** `data` excludes entries whose Codex visibility is `hide`
