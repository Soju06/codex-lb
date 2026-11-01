# Preserving Chat Completions tool strictness

OpenAI documents different omission defaults for the two protocols:
https://developers.openai.com/api/docs/guides/function-calling#strict-mode

Chat Completions defaults to non-strict function calling. Responses may normalize
an omitted strict flag into a strict schema; explicit false opts out. Copying
omission across that protocol boundary therefore does not preserve its meaning.

For example, `lookup_record(record_id, grep?, context?)` must retain optional
`grep` and `context` after chat conversion. Preserve the schema and materialize
the source protocol's default, rather than changing the schema or client tools.

The boundary is the chat-message conversion path. Responses-shaped passthrough
and native Responses requests already express destination-protocol semantics.
Their omitted flags must not be rewritten. Explicit true still follows the
existing strict-schema validation path.

The offline HTTP fixture captures the actual upstream request and returns an
ordinary success stream. It does not simulate schema normalization or generate
invalid arguments. The tests establish forwarding behavior, not a model failure
rate or a guarantee about every third-party backend.

PR #658 added explicit strict-tool validation and a test expecting omitted chat
strictness to stay omitted. This change intentionally replaces that literal
omission assertion with a non-strict-default assertion. Explicit true/false
preservation, local strict-schema rejection, and original error indices remain
covered by the existing strict-schema and HTTP compatibility suites.
