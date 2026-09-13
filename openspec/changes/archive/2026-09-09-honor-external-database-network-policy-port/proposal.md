## Why

External PostgreSQL installs can declare a non-default port, but the Helm
NetworkPolicy still permits only TCP 5432. With NetworkPolicy enabled, this can
block both application startup and migration access to an otherwise valid
external database.

## What Changes

- Render the configured external database port in the external PostgreSQL
  egress rule, or derive it from a direct external database URL.
- Add real Helm-rendering regression coverage for custom and default ports.
- Preserve bundled PostgreSQL egress, workload selectors, and additional
  operator-defined egress rules.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `deployment-installation`: Require external database NetworkPolicy egress to
  use the same configured port as the generated database URL.

## Impact

The Helm NetworkPolicy template and its focused rendering tests change. No
application API, chart dependency, setting, or default changes.
