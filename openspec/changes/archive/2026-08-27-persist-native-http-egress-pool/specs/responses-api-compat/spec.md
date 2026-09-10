## MODIFIED Requirements

### Requirement: Native direct HTTP egress preserves Responses streaming semantics

Direct Responses HTTP/SSE requests sent through native egress MUST preserve the existing normalized upstream payload and headers, rate-limit header ingestion, maximum SSE event size, idle and total request deadlines, terminal-event requirements, downstream event normalization, archives, and error envelope behavior. Downstream cancellation MUST cancel and await only the owned native request task, unregister its event stream, and leave unrelated multiplexed requests usable. Native transport selection MUST NOT change the public HTTP status or SSE framing contract.

#### Scenario: Native SSE response uses the ordinary parser

- **GIVEN** native direct egress returns an HTTP success and streamed SSE chunks
- **WHEN** the proxy consumes the response
- **THEN** chunks pass through the ordinary SSE parser, normalizer, terminal-event detection, and archive path
- **AND** the downstream event sequence matches the Python transport contract

#### Scenario: Downstream cancellation owns helper cleanup

- **GIVEN** one native helper generation owns multiple active requests
- **WHEN** one downstream stream is cancelled or closed before its terminal event
- **THEN** only that native request task is cancelled and awaited
- **AND** the helper and unrelated request streams remain usable
- **AND** the cancelled POST is not replayed through another HTTP client
