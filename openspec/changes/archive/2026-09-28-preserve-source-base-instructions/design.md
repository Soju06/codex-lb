## Context

`_to_upstream_model` retains source metadata in `UpstreamModel.raw`, but the
Codex serializer excludes `base_instructions` from raw extras and emits the
first-class field. The projection must populate that field before serialization.

## Decisions

Use the existing `base_instructions` field and accept only strings. Preserve
their contents exactly, including whitespace and Unicode. Missing, null and
other non-string values keep the existing empty-string default so malformed
metadata does not make the catalog unparseable.

The existing metadata passthrough remains responsible for fields such as
`tool_mode` and `multi_agent_version`. This change neither infers tool support
nor rewrites request instructions.

## Verification

Unit tests cover valid strings and malformed JSON values. Route regressions
create a source through the dashboard API and inspect both Codex catalog routes. A valid supplied prompt must fail against
the original projection and pass after the fix.
