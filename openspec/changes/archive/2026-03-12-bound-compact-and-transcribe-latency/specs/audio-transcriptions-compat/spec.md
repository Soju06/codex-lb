## ADDED Requirements

### Requirement: Transcription proxy requests use a bounded retry budget
The system MUST enforce a configurable total request budget for transcription proxy routes across account selection, token refresh, upstream connect, and upstream response handling. Once that budget is exhausted, the proxy MUST stop retrying and return a stable OpenAI-format timeout failure instead of waiting through repeated hard-coded timeout windows.

#### Scenario: Transcription budget expires before retry
- **WHEN** a transcription request consumes its configured request budget before a retry attempt can begin
- **THEN** the service returns `502` with OpenAI-format error code `upstream_unavailable`
- **AND** no further upstream attempt starts

#### Scenario: 401 transcription retry respects remaining budget
- **WHEN** the first transcription attempt returns 401 and token refresh succeeds while request budget remains
- **THEN** the retry uses the refreshed account metadata
- **AND** the retry only proceeds if enough request budget remains for another attempt
