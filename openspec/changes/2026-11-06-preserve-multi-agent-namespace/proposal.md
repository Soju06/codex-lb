## Why

Imported custom models omitted upstream metadata such as `multi_agent_version`,
so Codex did not assemble the collaboration tools. Even with that metadata
present, the source request filter drops namespace tools unless an operator
separately opts into that wire type.

## What Changes

- Treat the Responses `namespace` tool as supported for source models that
explicitly advertise a multi-agent version, so collaboration tool
declarations reach the upstream HTTP endpoint.

- Add regression coverage for forwarded collaboration tools.

## Capabilities

### Modified Capabilities

- `responses-api-compat`
