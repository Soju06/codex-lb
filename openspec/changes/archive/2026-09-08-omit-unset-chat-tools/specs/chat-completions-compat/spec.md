## ADDED Requirements

### Requirement: Chat Completions omit unset tools on the mapped Responses payload

When a `/v1/chat/completions` request omits the top-level `tools` field, the mapped Responses request MUST leave `tools` unset and the forwarded upstream payload MUST NOT include `tools`. An explicit client-sent empty `tools` array MUST still be forwarded as `[]`.

#### Scenario: Omitted chat tools stay omitted upstream

- **GIVEN** a Chat Completions request with `messages` and no `tools` field
- **WHEN** the service maps the request to Responses and forwards it
- **THEN** `tools` is absent from the mapped request field set
- **AND** the upstream payload does not include `tools`

#### Scenario: Explicit empty chat tools stay explicit

- **GIVEN** a Chat Completions request that sends `"tools": []`
- **WHEN** the service maps the request to Responses
- **THEN** the mapped payload includes `"tools": []`
