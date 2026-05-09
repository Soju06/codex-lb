## MODIFIED Requirements

### Requirement: Upstream Responses request size budget
The service SHALL enforce the configured upstream `response.create` websocket
request size budget before sending a request upstream. When a request exceeds
the budget, the service SHALL first slim historical inline images and historical
tool outputs. If the request is still too large and contains historical input
items before the recent user-turn suffix, the service SHALL omit the oldest
historical input items until the serialized request fits the budget or no
historical items remain. The service SHALL preserve the recent user-turn suffix
and SHALL include an assistant notice describing the number of omitted
historical input items.

#### Scenario: oversized historical text can be omitted
- **WHEN** a Codex `response.create` request remains over the websocket request
  size budget after image and tool-output slimming
- **AND** the request has historical input items before the recent user-turn
  suffix
- **THEN** the proxy omits the oldest historical input items until the request
  fits the budget
- **AND** the recent user-turn suffix remains in the upstream request
- **AND** the upstream request includes an assistant notice with the omitted
  item count

### Requirement: Oversized Responses request diagnostics
The service SHALL write oversized `response.create` debug dumps under the
configured app home directory by default. Container installs SHALL keep using
the container data directory, while local user installs SHALL use the user app
data directory.

#### Scenario: local install dump directory is writable by the user
- **WHEN** codex-lb runs outside a container
- **THEN** oversized `response.create` debug dumps default under the user's
  app data directory instead of `/var/lib/codex-lb`
