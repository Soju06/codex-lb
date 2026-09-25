## Why

Imported custom models omitted upstream metadata such as `multi_agent_version`,
so Codex did not assemble the collaboration tools. Even with that metadata
present, the source request filter drops namespace tools unless an operator
separately opts into that wire type. Source base instructions are also lost
when projecting metadata into the client catalog.

## What Changes

- Project source base instructions into the Codex model catalog and protect
  the existing passthrough of Codex capability metadata with regression tests.
- Treat the Responses `namespace` tool as supported for source models that
  explicitly advertise a multi-agent version, so collaboration tool
  declarations reach the upstream HTTP endpoint.
- Add regression coverage for catalog metadata and forwarded collaboration
  tools.

## Capabilities

### Modified Capabilities

- `model-catalog-compat`
- `responses-api-compat`
