## MODIFIED Requirements

### Requirement: Responses Lite follow-up transformations fail closed

The service MUST allow compact trimming to demote historical generated
goal/plan state anchors when the full required state set cannot fit the
upstream wire budget. This lossy fallback applies only to older generated
state-tool anchors (`create_goal`, `get_goal`, `update_goal`, `update_plan`)
and generated goal/plan context-marker messages.

The service MUST preserve the newest generated state anchor for each state
kind, structural Lite/developer/system state, terminal required items, and
side-effecting tails. When an omitted state anchor is a tool call, compact
trimming MUST omit its matching output with it rather than emitting an unpaired
call or output. If the preserved current or structural state still cannot fit,
the service MUST return `responses_compact_input_too_large`.

#### Scenario: Historical generated state anchors are demoted oldest first

- **WHEN** an oversized compact input contains enough historical generated
  goal/plan state-tool pairs that the required set alone exceeds the compact
  wire budget
- **THEN** compact trimming may omit the oldest generated state pairs until the
  payload fits
- **AND** the newest generated state pair, latest request, and any retained
  call/output pairs remain present and reconciled

#### Scenario: Current state remains fail closed

- **WHEN** the current generated state anchor or structural Lite/developer state
  cannot fit the compact wire budget
- **THEN** the service returns `responses_compact_input_too_large`
- **AND** it does not emit a malformed or unpaired transcript
