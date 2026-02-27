## ADDED Requirements

### Requirement: SDK-backed Anthropic Messages API compatibility
The service MUST accept `POST /claude/v1/messages` and execute generation via the
official local Claude SDK runtime path. The service MUST expose Anthropic-style
response envelopes for streaming and non-streaming calls.

#### Scenario: Claude-compatible request is handled by SDK transport
- **WHEN** a client sends a valid Anthropic Messages payload to `/claude/v1/messages`
- **THEN** the service maps the request to Claude SDK query execution and
  returns an Anthropic-compatible response envelope

#### Scenario: SDK transport error is surfaced
- **WHEN** Claude SDK runtime returns an execution error
- **THEN** the service returns an Anthropic-style error envelope with a mapped
  HTTP status code

### Requirement: Support streaming and non-streaming Messages responses
The service MUST support both `stream=true` (SSE) and non-streaming Messages
requests and MUST return Anthropic-compatible payloads/events.

#### Scenario: Streaming response
- **WHEN** `stream=true`
- **THEN** the service responds with `text/event-stream` and forwards upstream
  SSE events

#### Scenario: Non-streaming response
- **WHEN** `stream` is `false` or omitted
- **THEN** the service returns the upstream JSON message payload

### Requirement: Linux-only automatic credential discovery
When Anthropic mode is enabled, the service MUST support Linux-only automatic
discovery of Claude OAuth credentials from local host artifacts. The service
MUST allow explicit environment override and helper-command fallback.

#### Scenario: Discovered OAuth bearer token
- **WHEN** credential discovery finds a valid OAuth bearer token
- **THEN** the service uses that token for Anthropic usage-window fetches

#### Scenario: Missing credentials
- **WHEN** no credentials are available from discovery, helper command, or env
- **THEN** Anthropic usage refresh is skipped and proxy request handling remains
  available

### Requirement: Anthropic usage windows feed dashboard windows
The service MUST ingest Anthropic 5h and 7d usage windows and persist them in
the existing usage history model using `primary` (5h) and `secondary` (7d)
window semantics.

#### Scenario: 5h and 7d usage ingested
- **WHEN** Anthropic usage fetch succeeds
- **THEN** the service writes latest 5h usage to `primary` and 7d usage to
  `secondary` usage history rows

#### Scenario: Usage unavailable
- **WHEN** Anthropic usage fetch fails
- **THEN** request proxying remains available and the service keeps the latest
  persisted usage snapshot unchanged

### Requirement: Anthropic requests appear in dashboard request logs and stats
Anthropic `/claude/v1/messages` requests MUST be recorded in existing request logs with
model, latency, status/error, and token usage (when available), so dashboard
stats and trend charts include Anthropic traffic.

#### Scenario: Successful message request logged
- **WHEN** a proxied Anthropic request completes successfully
- **THEN** a request log row is recorded with `status=success` and usage fields
  when present

#### Scenario: Failed message request logged
- **WHEN** a proxied Anthropic request fails
- **THEN** a request log row is recorded with `status=error` and normalized
  error metadata
