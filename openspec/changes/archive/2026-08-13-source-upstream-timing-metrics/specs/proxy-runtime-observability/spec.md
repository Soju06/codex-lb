# proxy-runtime-observability Delta

## ADDED Requirements

### Requirement: Source-routed requests report upstream-measured generation timings

The proxy MUST record upstream-reported generation timing on the request log
for source-routed chat/responses/audio-transcription requests when the
OpenAI-compatible source's response body includes a `metrics` object with
`time_to_first_token_ms` and `generation_time_ms`. The proxy MUST set
`latency_first_token_ms` to the reported time-to-first-token and `latency_ms`
to the sum of time-to-first-token and generation time, using the same
request-log fields subscription-backed requests already populate. Sources
that do not return a `metrics` object MUST leave both fields `null`, and
negative or non-numeric values MUST be rejected rather than recorded.
Non-finite numeric values (`NaN`, positive infinity, or negative infinity)
MUST also be rejected rather than failing or interrupting the proxied request.

#### Scenario: Source metrics populate TTFT and total latency

- **GIVEN** an OpenAI-compatible source's chat completion response includes
  `metrics: {time_to_first_token_ms: 108.83, generation_time_ms: 162.98}`
- **WHEN** the request is logged
- **THEN** the request log's `latency_first_token_ms` is `109`
- **AND** the request log's `latency_ms` is `272`

#### Scenario: Streamed responses capture metrics from the final frame

- **GIVEN** a source-routed streaming chat completion whose final SSE frame
  carries both `usage` and `metrics`
- **WHEN** the stream completes successfully
- **THEN** the request log records the same `latency_first_token_ms` /
  `latency_ms` derived from that frame's `metrics`

#### Scenario: Missing metrics leaves latency fields null

- **GIVEN** an OpenAI-compatible source's response includes no `metrics` object
- **WHEN** the request is logged
- **THEN** `latency_ms` and `latency_first_token_ms` remain `null`, unchanged
  from prior behavior

#### Scenario: Dashboard retains generation-only throughput semantics

- **GIVEN** a source response reports `time_to_first_token_ms: 108.83`,
  `generation_time_ms: 162.98`, and `9` output tokens
- **WHEN** the existing dashboard computes tokens per second as output tokens
  divided by `latency_ms - latency_first_token_ms`
- **THEN** it reports approximately `55.2` generation tokens per second
- **AND** it does not substitute an upstream `tokens_per_second` value that may
  include TTFT

#### Scenario: Non-finite metrics are ignored safely

- **GIVEN** a source response contains `NaN` or infinity in either timing field
- **WHEN** the proxy parses the optional metrics
- **THEN** both timing values remain unset
- **AND** the otherwise successful proxied request is not interrupted
