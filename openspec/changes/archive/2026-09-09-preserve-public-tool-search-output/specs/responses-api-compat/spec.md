## ADDED Requirements

### Requirement: Public Responses preserve tool discovery output
The public Responses API SHALL preserve tool_search_output items, including their identifiers, execution mode, status, and loaded tool definitions, in output-item events, terminal response output, and collected JSON responses. Existing normalization of unrecognized output types SHALL remain unchanged.

#### Scenario: Discovery output arrives in streamed item events
- **WHEN** upstream emits tool_search_output in response.output_item.added or response.output_item.done
- **THEN** public streaming clients receive that item with its tool definitions intact

#### Scenario: Terminal output includes or omits discovered tools
- **WHEN** upstream completes with either populated output or an empty output list after emitting completed discovery items
- **THEN** public SSE terminal output and collected JSON output contain the completed tool_search_output item and loaded tool definitions
