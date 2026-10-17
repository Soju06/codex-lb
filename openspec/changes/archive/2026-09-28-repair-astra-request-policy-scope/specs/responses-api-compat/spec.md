## ADDED Requirements

### Requirement: Astra policy does not locally reject unrelated Responses controls

Subscription-backed Astra requests with valid configuration updates MUST
accept a client-supplied `truncation: "auto"` without forwarding that field
upstream. Automatic `context_management` compaction combined with updates
MUST remain invalid. Astra-specific configuration-update validation MUST NOT
reject `top_logprobs`, `logprobs`, or `message.output_text.logprobs` solely
because the model is Astra; generic Responses validation and upstream
capability decisions remain applicable.

#### Scenario: Anchored enforced effort with automatic truncation

- **GIVEN** an Astra continuation anchored by `previous_response_id` under an API key enforcing low effort
- **WHEN** the request includes `truncation: "auto"` and a user turn
- **THEN** both Responses routes accept the request and prepend the enforced low-effort update
- **AND** the subscription payload omits `truncation`

#### Scenario: Keyless logprobs controls

- **WHEN** a keyless Astra Responses request includes a valid logprobs control
- **THEN** the Astra-specific policy does not return a local invalid-request error for that control

#### Scenario: Automatic compaction remains incompatible

- **WHEN** an Astra request combines a configuration update and automatic `context_management` compaction
- **THEN** the proxy rejects it before upstream work with an invalid-request error
