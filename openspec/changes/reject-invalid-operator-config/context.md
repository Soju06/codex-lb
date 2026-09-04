# Operator config validation context

## Decision

Use Pydantic field constraints at the existing environment boundary and the
matching Helm JSON schema. Metrics port zero remains invalid because the
dedicated metrics endpoint is published as a stable service/scrape target.

## Constraints

- Preserve default metrics port 9090 and log format text.
- Preserve valid boundaries and main/metrics collision validation.
- Case-sensitive `text|json`; no alias or normalization.
- No formatter, metrics task, or template refactor.
