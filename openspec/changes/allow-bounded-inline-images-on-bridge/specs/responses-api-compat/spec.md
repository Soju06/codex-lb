# responses-api-compat delta

## ADDED Requirements

### Requirement: Bounded inline images retain bridge locality

With the inline-image flag enabled (default true), all-inline JPEG/PNG/WebP input images within the budgets SHALL use the HTTP responses bridge, preserving image bytes and prompt-cache identity. Unsupported shapes and image-generation tools SHALL retain existing routing.

#### Scenario: Text then screenshot then text
- **GIVEN** the bridge is enabled
- **WHEN** one thread sends text, inline screenshots (including tool outputs), then text
- **THEN** one bridge connection carries the requests and images remain verbatim
- **AND** every nested input_image is checked; external URL, file_id, unsupported media, or malformed base64 keeps existing bypass/reject behavior

### Requirement: Image admission uses decoded-size boundaries

Every admitted image SHALL be a non-empty strict base64 JPEG/PNG/WebP data URL decoding to at most 5,000,000 bytes inclusive. Encoded segments longer than 6,666,668 characters SHALL be rejected before decoding. Size-only routing SHALL NOT bypass the explicit rejection for supported inline shapes.

#### Scenario: Oversize image plus long history
- **WHEN** one image decodes to 5,000,001 bytes and total history exceeds the stock WS budget
- **THEN** the request fails before upstream send with HTTP400 payload_too_large, invalid_request_error, param=input
- **AND** no slot or reservation leaks and the next independent request proceeds

#### Scenario: Exact boundary
- **WHEN** an image decodes to exactly 5,000,000 bytes
- **THEN** it is admitted subject to the complete-frame budget

### Requirement: Complete frames preserve history under a local cap

An admitted image request SHALL be measured as the complete serialized response.create frame, including final metadata, against 67,108,864 bytes. Oversize SHALL fail before send without slimming history. Bridge sockets SHALL use the paired receive cap. This local cap SHALL NOT be represented as an upstream acceptance guarantee.

#### Scenario: Metadata crosses the frame limit
- **WHEN** a request passes the estimate but final metadata crosses the cap
- **THEN** it fails with HTTP400 payload_too_large before send

#### Scenario: Several legal images
- **WHEN** legal images plus history exceed the stock budget but fit the image-frame budget
- **THEN** local admission preserves all image bytes and history
- **AND** upstream1009 may still reject the frame

### Requirement: Rollback and intentional transport fallbacks remain available

Explicit false for CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INLINE_IMAGES_ENABLED SHALL restore blanket image bypass. Operator HTTP pin and recent-WS-failure fallback SHALL retain their existing logged behavior.

#### Scenario: Operator rollback
- **WHEN** the inline-image flag is false
- **THEN** image requests take the old bypass and log reason=image

### Requirement: Message-too-large closes are terminal payload failures

Close code1009, including adapter-generated local message-limit errors, SHALL be classified as non-retryable payload_too_large, not a network failure. It SHALL NOT rotate/exclude accounts or write error-health. Pending work and reservations SHALL settle normally.

#### Scenario: Before downstream commitment
- **WHEN** a dispatched request closes1009 before any response event
- **THEN** HTTP400 invalid_request_error with param=input is returned after exactly one dispatch
- **AND** the account can serve the next request

#### Scenario: After downstream commitment
- **WHEN** events were already streamed before1009
- **THEN** an SSE response.failed ends the stream without duplicating prior events

### Requirement: Unacknowledged image sends are not blindly replayed

For admitted images, a send attempt without response.created SHALL be treated as ambiguous unless explicit current-attempt rejection evidence permits existing proof-gated recovery. Silence, disconnect, or uncertain send exceptions SHALL NOT trigger automatic redispatch. Errors SHALL warn of possible ongoing execution and duplicate work. Text retry behavior SHALL remain unchanged.

#### Scenario: Silent request with healthy replacement available
- **WHEN** an image frame was sent and the upstream stays silent
- **THEN** the bounded deadline ends the request after one send attempt without using the replacement
- **AND** a later independent request succeeds after cleanup

#### Scenario: Explicit capacity rejection
- **WHEN** the upstream explicitly rejects the current image attempt for capacity
- **THEN** existing proof-gated recovery remains eligible
- **AND** evidence from that attempt cannot authorize replay after a later ambiguous send

#### Scenario: Generic or clean disconnect
- **WHEN** an unacknowledged image request disconnects without1009, including clean1000
- **THEN** it retains the existing error classification but is not blindly replayed
- **AND** text-only bounded retries remain unchanged
