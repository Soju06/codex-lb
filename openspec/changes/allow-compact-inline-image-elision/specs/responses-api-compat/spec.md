## MODIFIED Requirements

### Requirement: Responses Lite follow-up transformations fail closed

After a request is classified as Responses Lite shaped, the service MUST
preserve required Lite state through compact preparation, MUST validate the
final transformed compact input against the upstream JSON wire budget, and MUST
avoid permanently poisoning a thread when already-observed inline image bytes
alone make a required latest tool result too large. The image-byte relaxation
MUST NOT weaken fail-closed handling for oversized textual state.

#### Scenario: Oversized inline image does not poison terminal compaction

- **WHEN** compact input exceeds the upstream limit because a required latest
  eligible required tool output contains an inline data-URL image that the model already observed
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
