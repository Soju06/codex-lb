## ADDED Requirements

### Requirement: Reasoning summaries omit blank HTML comment placeholders

Responses reasoning output items and summary delta/part events MUST remove standalone blank HTML comment placeholder lines from `summary_text` before forwarding them to clients, including markers split across delta boundaries. This cleanup applies to both `/backend-api/codex/responses` and `/v1/responses` streamed or collected output item paths. The cleanup MUST be limited to reasoning summary text and MUST NOT rewrite placeholder-free whitespace, assistant-visible message content, inline blank comments, or non-empty HTML comments.

#### Scenario: Codex CLI route does not expose blank comment marker

- **GIVEN** upstream emits a reasoning output item with `summary: [{"type":"summary_text","text":"**Planning**\n\n<!-- -->"}]`
- **WHEN** a Codex CLI client streams `POST /backend-api/codex/responses`
- **THEN** the forwarded reasoning summary text is `**Planning**`
- **AND** the stream does not contain `<!-- -->`
