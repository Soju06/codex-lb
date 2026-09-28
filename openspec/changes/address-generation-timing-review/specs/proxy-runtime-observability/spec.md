## ADDED Requirements

### Requirement: Generation evidence preserves the verbatim streaming fast path

After a first non-reasoning output anchor has been established, collecting output
chunk evidence MUST NOT parse JSON for canonical output-delta frames eligible for
verbatim relay. The existing event classifier MUST supply the output event count.
Frames requiring payload inspection MUST retain normal parsing. Forwarded bytes
MUST remain unchanged.

#### Scenario: A long canonical stream has bounded parsing
- **GIVEN** a canonical stream with an established non-reasoning output anchor
- **WHEN** additional canonical output-delta frames arrive
- **THEN** each frame increments the output event count without a JSON parse
- **AND** the frames are forwarded verbatim

#### Scenario: Reasoning precedes the first non-reasoning output
- **GIVEN** visible reasoning has established TTFT but no non-reasoning output
- **WHEN** the first non-reasoning output arrives
- **THEN** its content is inspected once to establish the non-reasoning anchor
- **AND** subsequent eligible output-delta frames use event-type counting

### Requirement: Routing timing fallback does not fabricate generation evidence

A WebSocket finalizer without a recorded upstream terminal MUST preserve the
existing routing sampler's finalizer-entry clock fallback. Request-log upstream
terminal evidence MUST remain null when no terminal receipt was observed.

#### Scenario: Settlement follows a finalizer without a receipt timestamp
- **GIVEN** a request has a first token at two seconds and no terminal receipt timestamp
- **WHEN** its finalizer starts at twelve seconds and settles for ten seconds
- **THEN** routing throughput uses the ten-second generation span
- **AND** the stored total latency includes settlement
- **AND** the stored upstream terminal latency is null

## MODIFIED Requirements

### Requirement: Output speed sample evidence is preserved

New subscription-backed streaming logs MUST persist `latency_first_output_ms`, the first observed non-reasoning content time relative to the existing attempt/request-state anchor, and `output_delta_count`, the count of observed non-reasoning output events. After establishing the first content anchor, canonical output-delta frames MUST be counted by their event type without decoding their payloads. Text, refusal and actual tool arguments/input MUST qualify; reasoning and metadata-only lifecycle events MUST NOT qualify. Parsed empty deltas MUST NOT establish the first output anchor. These fields MUST remain nullable for historical and unsupported-source logs. Existing TTFT MAY still include visible reasoning or supported tool-start events and MUST be described as gateway-observed first output rather than model-internal or client end-to-end timing.

#### Scenario: Reasoning precedes actual output

- **GIVEN** a reasoning summary arrives at 200 ms and first text at 800 ms
- **WHEN** the request is logged
- **THEN** TTFT is 200 ms and first non-reasoning output latency is 800 ms
- **AND** TPS uses the non-reasoning output start

#### Scenario: Full terminal-only output

- **GIVEN** no streamed output has been observed and the terminal payload contains actual text or tool content
- **WHEN** that terminal event arrives
- **THEN** its receipt time may establish the first output and TTFT with one output chunk
- **AND** its TPS sample is insufficient
- **AND** positive usage alone MUST NOT synthesize first-output timestamps

