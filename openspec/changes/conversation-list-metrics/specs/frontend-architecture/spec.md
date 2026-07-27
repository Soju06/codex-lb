# Conversation list metrics

## ADDED Requirements

### Requirement: Conversation list exposes grouped request metrics

`GET /api/conversations` SHALL include `requestCount` and `firstRequest` for
every conversation. `requestCount` SHALL count all eligible request-log rows
in that conversation, and `firstRequest` SHALL be the earliest eligible
`requested_at`. Existing `lastRequest` SHALL remain the latest eligible
`requested_at`.

#### Scenario: A conversation aggregates request metrics

- **GIVEN** one conversation has eligible requests at 10:00, 10:07, and
  12:15
- **WHEN** the conversation list is requested
- **THEN** its `requestCount` is `3`
- **AND** its `firstRequest` is the 10:00 timestamp
- **AND** its `lastRequest` is the 12:15 timestamp
- **AND** warmup, limit-warmup, deleted, blank-ID, and otherwise ineligible
  rows do not affect those values

### Requirement: Conversation list renders metrics and readable duration

The dashboard SHALL render columns in this order: Last request, Lasted,
Conversation, Accounts, API key, Models, Requests, Tokens, Cost, Details.
The Lasted value SHALL use `lastRequest - firstRequest`, displaying `0s` for
zero duration, seconds for durations under one minute, `xm ys` for durations
under one hour, `xh ym` for durations under one day, and `xd yh` for durations
of at least one day. The conversation-ID cell SHALL be top-aligned.

#### Scenario: Duration uses two units and preserves zero

- **WHEN** a row spans 2 hours and 15 minutes
- **THEN** Lasted displays `2h 15m`
- **WHEN** a row spans 2 days and 3 hours
- **THEN** Lasted displays `2d 3h`
- **WHEN** firstRequest equals lastRequest
- **THEN** Lasted displays `0s`
