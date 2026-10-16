## Why

Model Sources can advertise `multi_agent_version` to Codex while the Responses
forwarder drops the resulting `namespace` tools. The client offers collaboration,
but the source never receives its tool definitions. Issue #2499 reports this
mismatch for both v1 and v2 collaboration.

## What Changes

- Treat a nonblank string `multi_agent_version` in a source model's raw metadata
  as a declaration that it supports `namespace` tools.
- Preserve complete namespace definitions and matching tool choices through the
  existing source capability filter, including when another tool is dropped.
- Keep the current conservative defaults and explicit
  `experimental_supported_tools` opt-in.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Amend the existing source tool-filtering contract to
  recognize `multi_agent_version` as a namespace capability declaration.

## Impact

The change affects Model Source metadata interpretation and source-routed
Responses payloads. It adds no settings, dependencies, schema changes, or client
runtime changes. The separate `base_instructions` problem in #2499 is outside
this change.

This proposal remains active pending maintainer agreement on the compatibility
contract. Refs #2499; it does not claim to resolve the whole issue.
