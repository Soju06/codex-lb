## ADDED Requirements

### Requirement: Account-scoped compact supports Responses-stream compaction

When the proxy sends an account-scoped remote compaction request upstream, it
MUST first use `POST /codex/responses` with streaming enabled and exactly one
final top-level input item of `{"type":"compaction_trigger"}`.

The proxy MUST collect exactly one `response.output_item.done` item whose type
is `compaction` or `compaction_summary`, together with a terminal
`response.completed` event, before returning compact success.

The returned compact item MUST preserve the upstream encrypted content and,
when present, its original `id` and `status`.

#### Scenario: Current upstream compaction stream succeeds

- **GIVEN** upstream returns a Responses stream containing one compaction
  output item and `response.completed`
- **WHEN** the proxy performs account-scoped compaction
- **THEN** the proxy returns a compact response containing that item
- **AND** the original encrypted content and item id are preserved

### Requirement: Legacy compact endpoint remains a compatibility fallback

If the upstream Responses-stream compaction request is rejected with an
unsupported-route status (`404`, `405`, or `501`), the proxy MUST retry the
same compact operation through the legacy `POST /codex/responses/compact`
JSON endpoint.

The proxy MUST NOT use the legacy fallback for client-payload, authentication,
quota, or ordinary upstream execution errors.

#### Scenario: Stream endpoint is unavailable

- **GIVEN** upstream returns `404` for the Responses-stream compaction request
- **WHEN** the proxy performs account-scoped compaction
- **THEN** the proxy retries through `/codex/responses/compact`
- **AND** a valid legacy response is returned as compact success
