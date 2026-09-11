## ADDED Requirements
### Requirement: Explicit local LLMBox authentication
A source created with kind `llmbox` SHALL be disabled initially and SHALL use only the fixed `https://llmbox.bytedance.net/v1` endpoint. It MUST NOT accept a supplied API key or route local credentials to another origin. Source kind SHALL be immutable. Each dispatched request SHALL read the current server user's LLMBox login cache; missing or malformed credentials MUST fail before contacting upstream, without starting login or falling back to another identity. The local token MUST NOT appear in API responses or forwarded upstream HTTP error bodies.

#### Scenario: Create an opt-in source
- **WHEN** an operator creates a LLMBox source
- **THEN** its kind is llmbox and it is disabled until explicitly enabled
- **AND** existing sources retain their previous behavior

#### Scenario: Credentials rotate
- **WHEN** the local login cache changes between requests
- **THEN** the next request uses the updated token without restarting the service

#### Scenario: Destination changes
- **WHEN** an operator changes a LLMBox source to another base URL or supplies an API key
- **THEN** the update is rejected

### Requirement: Native Responses routing
An enabled LLMBox source SHALL route only explicitly configured models and capabilities through the existing native Responses lifecycle. This feature MUST NOT configure account-pool overflow or relabel a dynamic model as a fixed model. Embeddings and audio SHALL remain unsupported for this source kind until separately verified.

#### Scenario: Stream a tool call
- **WHEN** an explicitly selected LLMBox model emits Responses function-call events
- **THEN** the proxy preserves the call ID, arguments and terminal event for tool-result continuation

### Requirement: Honest company quota presentation
The model-source dashboard SHALL distinguish credential-cache presence from authenticated upstream health. It SHALL show upstream remaining quota and reset time as unknown when no authoritative quota interface has been verified. Local usage SHALL be labelled as observations from retained request logs over the last 24 hours, with known token totals and requests missing complete token usage reported separately. Missing usage MUST NOT be presented as zero consumption or used to infer remaining quota.

#### Scenario: No quota API
- **WHEN** a LLMBox source is displayed
- **THEN** remaining quota and reset time are unknown and no quota percentage is fabricated
- **AND** local observed usage is displayed separately
