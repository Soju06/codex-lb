## ADDED Requirements

### Requirement: Merge upstream Responses fixes with local continuity protections

The merged Responses implementation MUST include upstream fixes for strict JSON schema validation, unsupported advisory parameter stripping, `top_p` and `temperature` handling, output text deltas, SSE keepalive comments, transient stream retries, image/file input handling, and oversized history behavior. These changes MUST NOT weaken the existing local continuity protection that keeps continuity-bearing Codex requests on eligible ChatGPT-web paths instead of unsafe Platform fallback paths.

#### Scenario: Streaming response remains alive and complete

- **WHEN** upstream streaming is slow but still active
- **THEN** the service may emit SSE keepalive comments
- **AND** it forwards text deltas and terminal completion or failure events in order

#### Scenario: Unsupported advisory parameters are removed before forwarding

- **WHEN** a valid Responses or Chat-mapped request contains upstream-unsupported advisory parameters such as `temperature` or `top_p`
- **THEN** the merged service removes those fields before forwarding
- **AND** unrelated supported fields remain preserved

#### Scenario: Continuity-bearing Codex request avoids unsafe Platform fallback

- **GIVEN** a request depends on continuity state such as `previous_response_id`, prompt-cache locality, or owner-forwarded bridge state
- **WHEN** the merged routing code evaluates fallback eligibility
- **THEN** the request remains on an eligible continuity-preserving route
- **AND** the merge does not reintroduce stateless Platform fallback for that continuity-bearing request

### Requirement: Merge file and image references into Responses routing

Responses requests that include supported file or image references MUST be normalized and routed using upstream-compatible handling while preserving existing validation and account-affinity rules.

#### Scenario: input_image file reference is handled by the merged contract

- **WHEN** a Responses request includes an image reference shape supported by the merged upstream contract
- **THEN** the service normalizes or rejects the reference according to the merged spec
- **AND** tests cover the selected behavior so file-id and image-url handling cannot drift silently
