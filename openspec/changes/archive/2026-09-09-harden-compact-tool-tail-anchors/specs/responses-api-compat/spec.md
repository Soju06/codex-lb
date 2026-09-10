## MODIFIED Requirements

### Requirement: Responses Lite follow-up transformations fail closed

After a request is classified as Responses Lite shaped, the service MUST preserve required Lite state through compact preparation, MUST validate the final transformed compact input against the upstream JSON wire budget, MUST reject policy rewrites to catalog-confirmed non-Lite models, and MUST suppress replayed code-mode side effects without collapsing distinct call identities. Compact trimming MAY omit a complete terminal non-state, non-side-effecting tool pair only when the pair plus required anchors and trim markers cannot fit the upstream wire budget. A latest output anchored by `previous_response_id` or a non-empty `conversation` remains required only when its matching call is absent from supplied input. A supplied call matches an output only when both `call_id` and the function/custom/apply-patch protocol variant are compatible. An unmatched latest tool call and a terminal tool call or matching pair classified as side-effecting by the canonical tool-safety classifier remain required compact context and MUST fail closed with `responses_compact_input_too_large` when they cannot fit. These guards MUST NOT weaken the body-derived Lite signal or trusted previous-response linkage rules. When already-observed inline image bytes alone make a required latest tool result too large, compact preparation MUST replace only those image bytes with an explicit textual omission marker rather than permanently poisoning the thread; this image-byte relaxation MUST NOT weaken fail-closed handling for oversized textual state.

#### Scenario: Oversized compact input keeps the Lite prelude
- **WHEN** compact input trimming is required for a Responses Lite request
- **THEN** every required `additional_tools` item remains in the upstream input
- **AND** typed and role-only system/developer state remains in the upstream input

#### Scenario: Compact input keeps a latest tool pair that fits
- **WHEN** compact trimming is required, the latest input item is a non-state, non-side-effecting tool call or tool output, and its complete pair fits with required anchors and trim markers
- **THEN** the latest item remains in the upstream input
- **AND** any matching call or output present in the supplied input is retained with it

#### Scenario: Oversized non-state tool tail leaves room for trim markers
- **WHEN** the latest input item is a non-state, non-side-effecting tool call or output whose complete pair cannot fit with required anchors and trim markers
- **THEN** the service omits the call and output together and represents the omission with a compact-trim marker
- **AND** it does not return `responses_compact_input_too_large` solely because the pair fit before marker framing
- **AND** the marker does not claim omitted terminal context was preserved

#### Scenario: Continuity-anchored latest tool output remains required
- **WHEN** a compact request carries `previous_response_id` or a non-empty `conversation` and its latest input item is a tool output without a matching call in the supplied input
- **THEN** the output remains in the upstream input because its call belongs to the prior response
- **AND** the service returns `responses_compact_input_too_large` when that required output cannot fit

#### Scenario: Ordinary non-patch paired tail may be omitted
- **WHEN** a compact request carries `previous_response_id` or a non-empty `conversation` and its latest ordinary, non-`apply_patch` tool output has a matching call in supplied input
- **THEN** compact trimming MAY omit the complete pair when it cannot fit
- **AND** this allowance does not apply to an `apply_patch` call or output

#### Scenario: Reused call ID from another tool variant does not satisfy continuity
- **WHEN** a compact request carries `previous_response_id` or a non-empty `conversation` and its latest tool
  output reuses the `call_id` of an incompatible function/custom/apply-patch
  call variant in supplied input
- **THEN** the latest output remains required as continuity from the previous response
- **AND** the incompatible supplied call is not retained as its pair

#### Scenario: Oversized latest unmatched tool call fails closed
- **WHEN** the latest compact input item is an unmatched tool call that cannot fit the compact wire budget
- **THEN** the service returns `responses_compact_input_too_large` rather than representing the call with a compact-trim marker

#### Scenario: Side-effecting tail remains required
- **WHEN** the latest compact input item is an `apply_patch_call`, `apply_patch_call_output`, or a tool call or matching pair classified as side-effecting by the canonical tool-safety classifier
- **THEN** the item and any matching counterpart remain required compact context
- **AND** the service returns `responses_compact_input_too_large` rather than omitting the side-effecting patch record when they cannot fit

#### Scenario: Reused call IDs keep only the required occurrence
- **WHEN** an older tool call and a required state-tool call reuse the same call ID
- **THEN** compact trimming retains the output matched to the required state-call occurrence
- **AND** it does not retain an oversized historical output solely because its earlier call reused that ID

#### Scenario: Exact-budget backtracking drops an optional tool pair together
- **WHEN** optional tool context fits the approximate item budget but trim-marker framing exceeds the exact wire cap
- **THEN** backtracking removes the optional call and its matching output as one group
- **AND** it does not re-add either counterpart while preserving every required item

#### Scenario: Oversized inline image does not poison terminal compaction
- **WHEN** compact input exceeds the upstream limit because a required latest eligible tool output contains an inline data-URL image that the model already observed
- **THEN** compact preparation retains the tool call and output identities
- **AND** first uses lossless context trimming when that can fit the request
- **AND** replaces only the inline image bytes with an explicit textual omission marker
- **AND** replaces an eligible legacy Chat `image_url` content part as a whole
  with a schema-valid text part before any generic string substitution
- **AND** preserves the other textual parts of the tool output
- **AND** accepted file-backed `input_file` references remain unchanged
- **AND** hosted `computer_call_output` screenshots remain fail-closed until a
  schema-valid compact placeholder is defined
- **AND** non-image required content that cannot fit still returns
  `responses_compact_input_too_large`
