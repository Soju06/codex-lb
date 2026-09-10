## ADDED Requirements

### Requirement: Native direct HTTP egress preserves Responses streaming semantics

Direct Responses HTTP/SSE requests sent through native egress MUST preserve the existing normalized upstream payload and headers, rate-limit header ingestion, maximum SSE event size, idle and total request deadlines, terminal-event requirements, downstream event normalization, archives, and error envelope behavior. Downstream cancellation MUST terminate and await the owned helper process. Native transport selection MUST NOT change the public HTTP status or SSE framing contract.

#### Scenario: Native SSE response uses the ordinary parser

- **GIVEN** native direct egress returns an HTTP success and streamed SSE chunks
- **WHEN** the proxy consumes the response
- **THEN** chunks pass through the ordinary SSE parser, normalizer, terminal-event detection, and archive path
- **AND** the downstream event sequence matches the Python transport contract

#### Scenario: Downstream cancellation owns helper cleanup

- **GIVEN** a native helper process owns an active Responses stream
- **WHEN** downstream consumption is cancelled or closed before the terminal event
- **THEN** the helper process is terminated and awaited
- **AND** the POST is not replayed through another HTTP client solely because of cancellation
