## Decisions

Use the existing source model `raw_metadata_json` contract. No new setting,
schema migration, model alias, or upstream catalog polling is needed.
`base_instructions` must be projected to the first-class `UpstreamModel`
field because the Codex serializer excludes that key from raw extras. Other
Codex capability fields already flow through raw extras.

A non-blank string `multi_agent_version` implies support for namespace tool
declarations. Derive that capability in `source_model_supported_tool_types`
so all callers use the same source declaration. Keep the existing explicit
`experimental_supported_tools` override and conservative default for sources
that do not declare either capability. Do not rewrite reserved collaboration
schemas or replay namespace handling.

## Operational repair

Restore the six affected models' capability metadata from their upstream
catalog and update the local Codex catalog. Existing running replicas can
forward namespace tools using their existing explicit `namespace` opt-in in
`experimental_supported_tools`; the code change makes this redundant after
deployment. Keep HTTP transport and `use_responses_lite=false` for this source.
The source credential and account selection are unchanged.

## Validation

Cover native and OpenAI-compatible catalog routes, both HTTP Responses routes,
valid and malformed capability declarations, retained namespace choices, and
continued removal of undeclared hosted tools. Validate with a real Codex CLI
parent/child session against the configured source. Persistent sessions are
required for the CLI smoke test: an ephemeral parent cannot be forked and
returns a client-side `no thread with id` error before creating a child.
