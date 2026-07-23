## ADDED Requirements

### Requirement: Public synthetic Responses failures carry numeric sequences

Public streaming `POST /v1/responses` MUST emit every terminal
`response.failed` with a finite integer `sequence_number` so
strict OpenAI SDK Responses parsers recognize the terminal failure. If the
upstream or proxy-generated event omits a finite integer sequence, the public
normalizer MUST assign the next sequence after all finite integer sequences it
has observed in the same downstream stream. If no finite integer sequence has
been observed, numbering MUST begin at zero.

The public normalizer MUST preserve an existing finite integer
`sequence_number` and advance its next-sequence watermark accordingly. This
repair MUST NOT change Codex-private backend stream shapes.

#### Scenario: Bridge failure after reasoning remains parseable

- **GIVEN** public `/v1/responses` has emitted sequenced reasoning events
- **WHEN** the upstream bridge closes before a terminal response
- **THEN** the downstream terminal `response.failed` carries the next numeric
  `sequence_number`
- **AND** a strict OpenAI SDK parser recognizes it as a terminal failure

#### Scenario: Failure without prior sequence starts at zero

- **GIVEN** public `/v1/responses` has not emitted a finite integer sequence
- **WHEN** the proxy emits a terminal `response.failed` without one
- **THEN** the terminal event carries `sequence_number = 0`

#### Scenario: Valid upstream failure sequence remains unchanged

- **GIVEN** an upstream terminal `response.failed` carries a finite integer
  `sequence_number`
- **WHEN** the public normalizer forwards the event
- **THEN** it preserves that sequence number unchanged

#### Scenario: Backend Codex stream shape remains unchanged

- **GIVEN** a Codex-private backend Responses stream carries an unsequenced
  terminal failure
- **WHEN** the stream is served without the public OpenAI SDK contract
- **THEN** the proxy does not add a public compatibility sequence
