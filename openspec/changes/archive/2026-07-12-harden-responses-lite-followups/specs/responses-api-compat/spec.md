## ADDED Requirements

### Requirement: Responses Lite follow-up transformations fail closed

After a request is classified as Responses Lite shaped, the service MUST preserve required Lite state through compact preparation, MUST validate the final transformed compact input against the upstream JSON wire budget, MUST reject policy rewrites to catalog-confirmed non-Lite models, and MUST suppress replayed code-mode side effects without collapsing distinct call identities. These guards MUST NOT weaken the body-derived Lite signal or trusted previous-response linkage rules.

#### Scenario: Oversized compact input keeps the Lite prelude

- **WHEN** compact input trimming is required for a Responses Lite request
- **THEN** every required `additional_tools` item remains in the upstream input
- **AND** typed and role-only system/developer state remains in the upstream input

#### Scenario: Compact input keeps a latest tool pair that fits

- **WHEN** compact trimming is required, the latest input item is a tool call or tool output, and its complete pair fits the wire budget
- **THEN** the latest item remains in the upstream input
- **AND** any matching call or output present in the supplied input is retained with it

#### Scenario: Oversized non-state tool tail does not block compaction

- **WHEN** the latest input item is a non-state tool call or output whose complete pair cannot fit the compact wire budget
- **THEN** the service omits the call and output together and represents the omission with a compact-trim marker
- **AND** user messages and state-tool items remain required fail-closed anchors

#### Scenario: Reused call IDs keep only the required occurrence

- **WHEN** an older tool call and a required state-tool call reuse the same call ID
- **THEN** compact trimming retains the output matched to the required state-call occurrence
- **AND** it does not retain an oversized historical output solely because its earlier call reused that ID

#### Scenario: Exact-budget backtracking drops an optional tool pair together

- **WHEN** optional tool context fits the approximate item budget but trim-marker framing exceeds the exact wire cap
- **THEN** backtracking removes the optional call and its matching output as one group
- **AND** it does not re-add either counterpart while preserving every required item

#### Scenario: Final compact wire expansion is rejected locally

- **WHEN** Unicode escaping, JSON array framing, or image inlining makes the final compact input exceed the upstream limit
- **THEN** the service returns `responses_compact_input_too_large` before an upstream attempt
- **AND** any API-key reservation is released
- **AND** no upstream account is penalized

#### Scenario: Terminal compaction trigger validates before admission

- **WHEN** a streaming Responses request ends with `compaction_trigger` and its derived compact input cannot fit
- **THEN** the service returns the same invalid-client-payload response before admission, reservation, account selection, or upstream compact work

#### Scenario: Enforced non-Lite model rejects Lite input

- **WHEN** API-key policy rewrites Lite-shaped input to a model whose catalog metadata disables Responses Lite
- **THEN** the service rejects the request before any upstream HTTP or websocket attempt

#### Scenario: Replayed code-mode side effects are emitted once

- **WHEN** reconnect replay repeats the same code-mode `exec` or `collaboration` call identity
- **THEN** the downstream client receives that side-effecting call only once

#### Scenario: Distinct code-mode calls remain distinct

- **WHEN** request history has different call IDs with identical code-mode source text and matching outputs
- **THEN** every call and matching output remains in the forwarded history
