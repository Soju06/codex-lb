## ADDED Requirements

### Requirement: Dashboard conversations query uses a server-authoritative timeframe

The dashboard conversations query (`useConversations`) SHALL request the conversations list by sending the `timeframe` query parameter to `GET /api/conversations` and SHALL NOT synthesize or send a browser-clock-derived `since` timestamp on the polling or refetch path. The query identity (TanStack query key) and the outgoing API parameters for a given `(search, timeframe)` SHALL remain stable across automatic refetches triggered by polling, window focus, or re-mount, so that an unchanged filter selection reuses the same request and the same server-derived window rather than producing a new timestamp on every refetch. Changing the conversation timeframe SHALL trigger a refetch with the newly selected timeframe.

#### Scenario: Conversations polling sends timeframe and omits browser-generated since

- **GIVEN** the dashboard conversations view is mounted with `conversationTimeframe=7d` in the URL
- **WHEN** the 30-second polling refetch fires
- **THEN** the outgoing request to `GET /api/conversations` includes `timeframe=7d`
- **AND** the request does not include any `since` parameter generated from the browser clock

#### Scenario: Refetch reuses the same API parameters for an unchanged timeframe

- **GIVEN** the dashboard conversations view is mounted with `conversationTimeframe=7d` and `search=foo`
- **WHEN** automatic refetches fire multiple times without any filter change
- **THEN** every outgoing request carries the same `timeframe=7d` and `search=foo` parameters
- **AND** the query key for the conversations query does not change between those refetches

#### Scenario: Changing the conversation timeframe triggers a refetch with the new key

- **GIVEN** the dashboard conversations view is mounted with `conversationTimeframe=7d`
- **WHEN** the operator changes the conversation timeframe selector to `30d`
- **THEN** the URL `conversationTimeframe` parameter updates to `30d`
- **AND** the conversations query refetches with `timeframe=30d` under a new query key

#### Scenario: Standalone conversations view also uses server-authoritative timeframe

- **GIVEN** the conversations view is mounted without an injected dashboard state (the standalone mount path)
- **WHEN** the view reads the `conversationTimeframe` parameter from the URL and fetches
- **THEN** the outgoing request sends `timeframe=<key>` and no browser-generated `since`
- **AND** the standalone mount path produces the same server-authoritative behavior as the dashboard-embedded path

#### Scenario: Browser clock skew does not change the conversations request

- **GIVEN** the browser clock is skewed days ahead of or behind the server clock
- **WHEN** the dashboard conversations view fetches or refetches
- **THEN** the outgoing request parameters are unaffected by the browser clock
- **AND** only the `timeframe` parameter (and `search`/`limit`/`offset` when present) is sent
