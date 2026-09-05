## MODIFIED Requirements

### Requirement: Self-contained Codex context replay envelopes

The account-neutral fresh-replay validator SHALL recognize `reasoning.context=all_turns` requests containing fully supplied messages and additional-tools bundles with Codex-local UUID labels. It SHALL accept canonical UUID labels with `msg_`, `at_`, `fc_`, `fco_`, `ctc_` and `ctco_` prefixes on the corresponding message, additional-tools, function and custom-tool items. It SHALL also recognize exactly 50 lowercase hexadecimal characters after `msg_` on assistant messages and after `fc_` or `ctc_` on tool calls. Classification MAY omit these labels and validated transcript metadata from a temporary projection, but MUST NOT rewrite the dispatched body.

Transcript metadata SHALL contain only an optional nonblank `turn_id`, an optional finite nonnegative numeric `create_time`, and an optional nonempty list of nonblank `content_item_kinds`. The newly accepted client metadata keys SHALL be limited to nonblank string values for `session_id`, `thread_id`, `turn_id`, and `root_turn_id`, in addition to existing permitted keys.

Client tool namespaces SHALL contain only a nonblank name, an optional textual description, and a nonempty list of independently validated function or custom tool declarations. Unknown namespace fields, nested namespaces, and hosted-resource tools within a namespace MUST remain nonportable.

Unknown encrypted retained state, stored response/conversation references, input file IDs, container/vector-store references, incomplete tool exchanges, malformed local labels, and unknown metadata fields MUST continue to prevent cross-account replay. This requirement MUST NOT relax strict ownership or bypass API-key account scope, failure settlement or retry exclusions.

Authenticated context tool outputs MAY replace only verified native ciphertext parts in the classification projection. HTTP streaming and WebSocket request state SHALL carry this verification evidence internally and MUST NOT accept it from client fields. The upstream request MUST preserve the native ciphertext. A complete tool exchange with recognized namespace and transcript metadata SHALL remain eligible for the existing replay policy; unverified ciphertext and encrypted reasoning MUST remain fenced.

#### Scenario: Pre-visible quota rejection can change accounts
- **GIVEN** a fully supplied Codex context request containing only the recognized envelope and client-side tool declarations
- **WHEN** account A returns HTTP 429 before any output becomes visible
- **THEN** the existing retry policy may exclude A and submit the unchanged body to eligible account B

#### Scenario: An envelope also contains retained account state
- **GIVEN** a recognized Codex context envelope containing unverified encrypted retained input or a stored account-specific reference
- **WHEN** account A is rejected or becomes unavailable
- **THEN** the proxy does not submit that retained body to account B

#### Scenario: Verified context results survive pre-visible quota rotation
- **GIVEN** a complete tool exchange containing authenticated context output and no other account-specific state
- **WHEN** an HTTP or WebSocket request receives a pre-visible quota rejection
- **THEN** existing retry policy may send the same native ciphertext to another eligible account
- **AND** the request retains ordinary reservation settlement, file ownership and retry exclusions
