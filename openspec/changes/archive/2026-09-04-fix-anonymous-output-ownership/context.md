## Reproduction

Two requests share an upstream connection. Response A has an ID; request B awaits response.created. Output events for A carry item_id but no response_id. The anonymous matcher chooses B because it is the only unresolved request. The final response.completed event still resolves to A by response ID, so A completes without its streamed output.

The fix prefers a unique started response for output events. When none has started, supported pre-created output behavior remains unchanged. Metadata and error matching retain their existing paths. Multiple started responses remain unresolved rather than guessing ownership.

## Validation

On unmodified main, eight bridge-level output delivery regressions fail. With the patch, the affected HTTP bridge, cancellation, and WebSocket suites report 1,174 passing tests and one missing-table failure in the pre-existing file-affinity test. Ruff and targeted type checking pass.
