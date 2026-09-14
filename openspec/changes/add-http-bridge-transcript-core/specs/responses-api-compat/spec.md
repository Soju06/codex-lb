## ADDED Requirements

### Requirement: Transcript helper identity rules are deterministic

Transcript helpers MUST extract the stable `id`, `call_id`, and `type` fields
without mutating input. Tool calls MUST carry both `id` and `call_id`; tool
outputs MUST carry `call_id`; repeatable non-tool output items MUST carry
`id`, except for the identity-less `compaction` boundary marker.

#### Scenario: Tool and message identities are validated

- **WHEN** a helper validates a function call, function output, message, or
  compaction item
- **THEN** it accepts only the identity shape specified above
- **AND** it leaves the original item unchanged

### Requirement: Transcript echo matching and tool de-duplication fail closed

Echo matching MUST ignore provider-owned item ids and an omitted optional
status, but MUST reject conflicting explicit statuses or other content.
Exact repeated tool calls or outputs with the same call id MUST be removed only
when their type and canonical content match exactly; conflicting content MUST
return an ineligible result. Malformed JSON-compatible item types MUST not
raise while being inspected.

#### Scenario: Exact echoes are removed and conflicts are rejected

- **WHEN** a replay list contains an exact duplicate tool call or output
- **THEN** the duplicate is removed while order of remaining items is kept
- **WHEN** the same call id has different content or status
- **THEN** de-duplication returns an ineligible result
- **AND** an unhashable item type is preserved for normal validation
